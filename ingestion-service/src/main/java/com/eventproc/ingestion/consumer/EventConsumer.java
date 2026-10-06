package com.eventproc.ingestion.consumer;

import com.eventproc.ingestion.config.IngestionKafkaProperties;
import com.eventproc.ingestion.exception.SchemaValidationException;
import com.eventproc.ingestion.kafka.DeadLetters;
import com.eventproc.ingestion.metrics.IngestionMetrics;
import com.eventproc.ingestion.model.NormalizedEvent;
import com.eventproc.ingestion.service.NormalizationService;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.Timer;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.BatchListenerFailedException;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.kafka.support.SendResult;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Consumes {@code raw-events}, normalises each record and publishes it to
 * {@code normalized-events} keyed by {@code event_id}; invalid records go to the DLT.
 *
 * <p><b>Delivery (REQ-A):</b> at-least-once. Records of a poll are processed in order and
 * their produce requests pipelined; the batch is acknowledged ({@code MANUAL_IMMEDIATE})
 * only after the broker has confirmed every produced record. If a send fails or an
 * unexpected error occurs at index {@code i}, all earlier sends are awaited and a
 * {@link BatchListenerFailedException} for {@code i} is thrown: the error handler commits
 * offsets before {@code i}, retries from {@code i} with back-off and finally dead-letters it.
 *
 * <p><b>Validation (REQ-B):</b> contract violations are deterministic, so they are not
 * retried — the original bytes go straight to the DLT with an {@code x-error-reason} header
 * and a structured WARN log carrying partition and offset.
 *
 * <p><b>Normalisation (REQ-C):</b> delegated to {@link NormalizationService}.
 */
@Component
public class EventConsumer {

    private static final Logger log = LoggerFactory.getLogger(EventConsumer.class);

    private final NormalizationService normalizationService;
    private final KafkaTemplate<String, byte[]> kafkaTemplate;
    private final ObjectMapper objectMapper;
    private final IngestionMetrics metrics;
    private final IngestionKafkaProperties props;

    public EventConsumer(NormalizationService normalizationService,
                         KafkaTemplate<String, byte[]> kafkaTemplate,
                         ObjectMapper objectMapper,
                         IngestionMetrics metrics,
                         IngestionKafkaProperties props) {
        this.normalizationService = normalizationService;
        this.kafkaTemplate = kafkaTemplate;
        this.objectMapper = objectMapper;
        this.metrics = metrics;
        this.props = props;
    }

    @KafkaListener(
        id = "ingestion-consumer",
        topics = "${app.kafka.input-topic}",
        groupId = "${spring.kafka.consumer.group-id}",
        containerFactory = "kafkaListenerContainerFactory"
    )
    public void consume(List<ConsumerRecord<String, byte[]>> records, Acknowledgment acknowledgment) {
        List<PendingSend> pending = new ArrayList<>(records.size());
        for (int i = 0; i < records.size(); i++) {
            ConsumerRecord<String, byte[]> record = records.get(i);
            // Timed from parse until the broker confirms the send (see awaitAll),
            // so the REQ-A latency SLO sees produce/broker slowness too.
            Timer.Sample sample = metrics.startTimer();
            try {
                pending.add(handle(record, i, sample));
            } catch (RuntimeException ex) {
                metrics.stopTimer(sample);
                awaitAll(pending, records);
                throw new BatchListenerFailedException(
                        "unexpected error processing " + coordinates(record), ex, i);
            }
        }
        awaitAll(pending, records);
        acknowledgment.acknowledge();
    }

    // ── Internals ─────────────────────────────────────────────────────────

    private PendingSend handle(ConsumerRecord<String, byte[]> record, int index, Timer.Sample sample) {
        try {
            NormalizedEvent event = normalizationService.process(record.value());
            return new PendingSend(index, Outcome.VALID, sample,
                    kafkaTemplate.send(props.outputTopic(), event.getEventId(), serialize(event)));
        } catch (SchemaValidationException e) {
            log.warn("event=ingestion_rejected topic={} partition={} offset={} key={} errors={} reason=\"{}\"",
                    record.topic(), record.partition(), record.offset(), record.key(),
                    e.getErrors().size(), e.getMessage());
            return new PendingSend(index, Outcome.INVALID, sample,
                    kafkaTemplate.send(DeadLetters.record(props.deadLetterTopic(), record, e.getMessage())));
        }
    }

    private byte[] serialize(NormalizedEvent event) {
        try {
            return objectMapper.writeValueAsBytes(event);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("failed to serialise normalised event " + event.getEventId(), e);
        }
    }

    /**
     * Blocks until every pending send is acknowledged, recording outcome metrics.
     * On the first failure throws {@link BatchListenerFailedException} for that record.
     */
    private void awaitAll(List<PendingSend> pending, List<ConsumerRecord<String, byte[]>> records) {
        long timeoutMs = props.sendTimeout().toMillis();
        for (PendingSend p : pending) {
            try {
                p.future().get(timeoutMs, TimeUnit.MILLISECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new BatchListenerFailedException("interrupted awaiting send", e, p.index());
            } catch (ExecutionException | TimeoutException e) {
                Throwable cause = e instanceof ExecutionException && e.getCause() != null ? e.getCause() : e;
                throw new BatchListenerFailedException(
                        "send failed for " + coordinates(records.get(p.index())), cause, p.index());
            }
            metrics.stopTimer(p.sample());
            if (p.outcome() == Outcome.VALID) {
                metrics.valid();
            } else {
                metrics.invalid();
            }
        }
        pending.clear();
    }

    private static String coordinates(ConsumerRecord<?, ?> r) {
        return r.topic() + "-" + r.partition() + "@" + r.offset();
    }

    private enum Outcome { VALID, INVALID }

    private record PendingSend(int index, Outcome outcome, Timer.Sample sample,
                               CompletableFuture<SendResult<String, byte[]>> future) {}
}
