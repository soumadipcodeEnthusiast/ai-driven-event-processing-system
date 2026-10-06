package com.eventproc.ingestion.consumer;

import com.eventproc.ingestion.kafka.DeadLetters;
import com.eventproc.ingestion.service.NormalizationService;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.consumer.Consumer;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.OffsetAndMetadata;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.header.Header;
import org.apache.kafka.common.serialization.ByteArrayDeserializer;
import org.apache.kafka.common.serialization.ByteArraySerializer;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.SpyBean;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.ResponseEntity;
import org.springframework.kafka.core.DefaultKafkaConsumerFactory;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.test.EmbeddedKafkaBroker;
import org.springframework.kafka.test.context.EmbeddedKafka;
import org.springframework.kafka.test.utils.KafkaTestUtils;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.BooleanSupplier;
import java.util.function.Function;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

/**
 * End-to-end test against an embedded broker: valid events reach {@code normalized-events}
 * keyed by event_id, invalid ones reach the DLT with original bytes and contract headers,
 * an unexpected error is retried then dead-lettered, offsets are committed, and the
 * contract metrics are exported.
 */
@SpringBootTest(
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {
                "spring.kafka.bootstrap-servers=${spring.embedded.kafka.brokers}",
                "spring.kafka.listener.concurrency=1",
                "app.kafka.retry.initial-interval=20ms",
                "app.kafka.retry.max-interval=50ms",
                "app.normalization.strict-validation=true"
        })
@EmbeddedKafka(partitions = 1, topics = {"raw-events", "normalized-events", "raw-events.DLT"})
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class EventConsumerIT {

    private static final String BOOM_ID = "e-boom";
    private static final Duration TIMEOUT = Duration.ofSeconds(30);

    @Autowired EmbeddedKafkaBroker broker;
    @Autowired ObjectMapper objectMapper;
    @Autowired MeterRegistry meterRegistry;
    @Autowired TestRestTemplate rest;
    @SpyBean NormalizationService normalizationService;

    private KafkaTemplate<String, byte[]> producer;
    private Consumer<String, byte[]> outConsumer;
    private Consumer<String, byte[]> dltConsumer;

    @BeforeAll
    void setUp() {
        Map<String, Object> pp = KafkaTestUtils.producerProps(broker);
        pp.put("key.serializer", StringSerializer.class);
        pp.put("value.serializer", ByteArraySerializer.class);
        producer = new KafkaTemplate<>(new DefaultKafkaProducerFactory<>(pp));
        outConsumer = consumer("it-out", "normalized-events");
        dltConsumer = consumer("it-dlt", "raw-events.DLT");
    }

    @AfterAll
    void tearDown() {
        outConsumer.close();
        dltConsumer.close();
        producer.destroy();
    }

    private Consumer<String, byte[]> consumer(String group, String topic) {
        Map<String, Object> cp = KafkaTestUtils.consumerProps(group, "false", broker);
        cp.put("auto.offset.reset", "earliest");
        Consumer<String, byte[]> c = new DefaultKafkaConsumerFactory<>(cp,
                new StringDeserializer(), new ByteArrayDeserializer()).createConsumer();
        broker.consumeFromAnEmbeddedTopic(c, topic);
        return c;
    }

    @Test
    void routesValidInvalidAndFailingEvents() throws Exception {
        doAnswer(inv -> {
            byte[] v = inv.getArgument(0);
            if (v != null && new String(v, StandardCharsets.UTF_8).contains(BOOM_ID)) {
                throw new IllegalStateException("simulated downstream failure");
            }
            return inv.callRealMethod();
        }).when(normalizationService).process(argThat(v -> true));

        byte[] notUtf8 = {(byte) 0xff, (byte) 0xfe, 0x00, 0x01};
        List<ProducerRecord<String, byte[]>> input = List.of(
                rec("k1", "{\"event_id\":\"e-1\",\"timestamp\":\"2026-01-01T12:00:00+02:00\",\"source\":\"checkout-api\","
                        + "\"type\":\"http_request\",\"component_id\":\"ingestion-service\",\"payload\":{\"status\":500}}"),
                rec(null, "{\"event_id\":\"e-2\",\"timestamp\":\"2026-01-01T10:00:00Z\",\"source\":\"svc\"}"),
                rec("bad-1", "{\"timestamp\":\"2026-01-01T10:00:00Z\",\"source\":\"svc\"}"),               // missing event_id
                rec("bad-2", "not json at all"),
                rec("bad-3", "{\"event_id\":\"e-x\",\"timestamp\":\"2026-01-01T10:00:00\",\"source\":\"s\"}"), // no offset
                rec("bad-4", "{\"event_id\":\"e-y\",\"timestamp\":\"2026-01-01T10:00:00Z\",\"source\":\"s\",\"extra\":1}"),
                new ProducerRecord<>("raw-events", "bad-5", notUtf8),
                rec("boom", "{\"event_id\":\"" + BOOM_ID + "\",\"timestamp\":\"2026-01-01T10:00:00Z\",\"source\":\"s\"}"),
                rec("k3", "{\"event_id\":\"e-3\",\"timestamp\":\"2026-01-01T10:00:00-05:00\",\"source\":\"svc\",\"type\":\"t\"}"));
        for (ProducerRecord<String, byte[]> r : input) {
            producer.send(r).get();
        }

        // ── normalized-events ───────────────────────────────────────────
        List<ConsumerRecord<String, byte[]>> out = drain(outConsumer, 3);
        assertThat(out).extracting(ConsumerRecord::key).containsExactly("e-1", "e-2", "e-3");
        Map<String, JsonNode> byKey = new HashMap<>();
        for (ConsumerRecord<String, byte[]> r : out) {
            byKey.put(r.key(), objectMapper.readTree(r.value()));
        }
        JsonNode e1 = byKey.get("e-1");
        assertThat(e1.get("event_id").asText()).isEqualTo("e-1");
        assertThat(e1.get("timestamp").asText()).isEqualTo("2026-01-01T10:00:00Z");
        assertThat(e1.get("source").asText()).isEqualTo("checkout-api");
        assertThat(e1.get("type").asText()).isEqualTo("http_request");
        assertThat(e1.get("component_id").asText()).isEqualTo("ingestion-service");
        assertThat(e1.get("payload").get("status").asInt()).isEqualTo(500);
        assertThat(e1.get("schema_version").asText()).isEqualTo("1.0");
        assertThat(e1.get("ingested_at").asText()).matches("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d{1,3})?Z");
        JsonNode e2 = byKey.get("e-2");
        assertThat(e2.get("type").asText()).isEqualTo("generic");
        assertThat(e2.get("component_id").isNull()).isTrue();
        assertThat(e2.get("payload").isObject()).isTrue();
        assertThat(e2.get("payload").size()).isZero();
        assertThat(byKey.get("e-3").get("timestamp").asText()).isEqualTo("2026-01-01T15:00:00Z");

        // ── DLT ─────────────────────────────────────────────────────────
        List<ConsumerRecord<String, byte[]>> dlt = drain(dltConsumer, 6);
        Map<String, ConsumerRecord<String, byte[]>> dltByKey = dlt.stream()
                .collect(Collectors.toMap(ConsumerRecord::key, Function.identity()));
        assertThat(dltByKey.keySet()).containsExactlyInAnyOrder("bad-1", "bad-2", "bad-3", "bad-4", "bad-5", "boom");

        for (int i = 0; i < input.size(); i++) {
            ProducerRecord<String, byte[]> sent = input.get(i);
            ConsumerRecord<String, byte[]> d = sent.key() == null ? null : dltByKey.get(sent.key());
            if (d == null) continue;
            assertThat(d.value()).as("original bytes unchanged for %s", sent.key()).isEqualTo(sent.value());
            assertThat(header(d, DeadLetters.ORIGINAL_TOPIC)).isEqualTo("raw-events");
            assertThat(header(d, DeadLetters.ORIGINAL_PARTITION)).isEqualTo("0");
            assertThat(header(d, DeadLetters.ORIGINAL_OFFSET)).isEqualTo(Integer.toString(i));
            assertThat(header(d, DeadLetters.ERROR_REASON)).isNotBlank();
        }
        assertThat(header(dltByKey.get("bad-1"), DeadLetters.ERROR_REASON)).contains("event_id is required");
        assertThat(header(dltByKey.get("bad-2"), DeadLetters.ERROR_REASON)).contains("is not valid JSON");
        assertThat(header(dltByKey.get("bad-3"), DeadLetters.ERROR_REASON)).contains("timestamp");
        assertThat(header(dltByKey.get("bad-4"), DeadLetters.ERROR_REASON)).contains("extra is not an allowed field");
        assertThat(header(dltByKey.get("boom"), DeadLetters.ERROR_REASON))
                .isEqualTo("IllegalStateException: simulated downstream failure");

        // ── retries: 1 attempt + 3 retries for the failing record ──────
        verify(normalizationService, times(4)).process(argThat(v ->
                v != null && new String(v, StandardCharsets.UTF_8).contains(BOOM_ID)));

        // ── offsets committed past every input record ──────────────────
        awaitTrue(() -> committedOffset() == input.size(), "committed offset == " + input.size());

        // ── metrics ─────────────────────────────────────────────────────
        awaitTrue(() -> count("valid") == 3 && count("invalid") == 5 && count("error") == 1,
                "metric counts valid=3 invalid=5 error=1");
        assertThat(meterRegistry.get("ingestion.processing").timer().count()).isGreaterThanOrEqualTo(input.size());

        assertThat(rest.getForEntity("/actuator/health/liveness", String.class).getBody()).contains("UP");
        assertThat(rest.getForEntity("/actuator/health/readiness", String.class).getBody()).contains("UP");

        ResponseEntity<String> prom = rest.getForEntity("/actuator/prometheus", String.class);
        assertThat(prom.getStatusCode().is2xxSuccessful()).isTrue();
        assertThat(prom.getBody())
                .contains("ingestion_events_total{")
                .contains("outcome=\"valid\"")
                .contains("outcome=\"invalid\"")
                .contains("outcome=\"error\"")
                .contains("ingestion_processing_seconds_bucket{")
                .contains("ingestion_processing_seconds_count")
                .contains("kafka_consumer_fetch_manager_records_lag_max")
                .contains("jvm_memory_used_bytes")
                .contains("jvm_memory_max_bytes")
                .contains("process_cpu_usage")
                .contains("http_server_requests_seconds_count");
        assertThat(prom.getBody().lines())
                .as("SLO bucket le=0.5 on ingestion_processing_seconds")
                .anyMatch(l -> l.startsWith("ingestion_processing_seconds_bucket{") && l.contains("le=\"0.5\""));

    }

    // ── helpers ─────────────────────────────────────────────────────────

    private static ProducerRecord<String, byte[]> rec(String key, String json) {
        return new ProducerRecord<>("raw-events", key, json.getBytes(StandardCharsets.UTF_8));
    }

    private static String header(ConsumerRecord<?, ?> r, String name) {
        Header h = r.headers().lastHeader(name);
        assertThat(h).as("header %s", name).isNotNull();
        return new String(h.value(), StandardCharsets.UTF_8);
    }

    private static List<ConsumerRecord<String, byte[]>> drain(Consumer<String, byte[]> c, int expected) {
        List<ConsumerRecord<String, byte[]>> all = new ArrayList<>();
        long deadline = System.nanoTime() + TIMEOUT.toNanos();
        while (all.size() < expected && System.nanoTime() < deadline) {
            c.poll(Duration.ofMillis(200)).forEach(all::add);
        }
        // short grace period to detect unexpected extra records (e.g. duplicates)
        long grace = System.nanoTime() + Duration.ofMillis(500).toNanos();
        while (System.nanoTime() < grace) {
            c.poll(Duration.ofMillis(100)).forEach(all::add);
        }
        assertThat(all).as("records received").hasSize(expected);
        return all;
    }

    private double count(String outcome) {
        return meterRegistry.get("ingestion.events").tag("outcome", outcome).counter().count();
    }

    private long committedOffset() {
        try (AdminClient admin = AdminClient.create(Map.of(
                AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, broker.getBrokersAsString()))) {
            Map<TopicPartition, OffsetAndMetadata> offsets = admin
                    .listConsumerGroupOffsets("ingestion-consumer-group")
                    .partitionsToOffsetAndMetadata().get();
            OffsetAndMetadata om = offsets.get(new TopicPartition("raw-events", 0));
            return om == null ? -1 : om.offset();
        } catch (Exception e) {
            return -1;
        }
    }

    private static void awaitTrue(BooleanSupplier condition, String description) throws InterruptedException {
        long deadline = System.nanoTime() + TIMEOUT.toNanos();
        while (!condition.getAsBoolean()) {
            if (System.nanoTime() > deadline) {
                throw new AssertionError("timed out waiting for: " + description);
            }
            Thread.sleep(100);
        }
    }
}
