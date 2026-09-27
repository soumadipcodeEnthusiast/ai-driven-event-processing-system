package com.eventproc.ingestion.consumer;

import com.eventproc.ingestion.model.NormalizedEvent;
import com.eventproc.ingestion.service.NormalizationService;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer that ingests raw event messages from the configured input
 * topic and delegates to {@link NormalizationService} for validation and
 * normalisation.
 *
 * <p>Implements operations from the {@code IngestionService} class in the
 * class diagram.
 *
 * <p>Requirements covered:
 * <ul>
 *   <li>REQ-A — Real-time event ingestion from Kafka</li>
 *   <li>REQ-B — Schema validation prior to processing</li>
 * </ul>
 */
@Component
public class EventConsumer {

    private static final Logger log = LoggerFactory.getLogger(EventConsumer.class);

    private final NormalizationService normalizationService;

    @Autowired
    public EventConsumer(NormalizationService normalizationService) {
        this.normalizationService = normalizationService;
    }

    // ── Public API ────────────────────────────────────────────────────────

    /**
     * Consumes a raw event record from the Kafka input topic.
     *
     * <p><b>REQ-A</b>: The system shall ingest events from Kafka with
     * at-least-once delivery semantics and manual offset commit.
     *
     * <p><b>REQ-B</b>: The consumer shall validate the raw message against
     * the registered schema before forwarding to normalisation.
     *
     * @param record         Kafka consumer record containing the raw event JSON
     * @param acknowledgment Spring Kafka manual acknowledgment handle
     */
    @KafkaListener(
        topics = "${app.kafka.input-topic}",
        groupId = "${spring.kafka.consumer.group-id}",
        containerFactory = "kafkaListenerContainerFactory"
    )
    public void consume(ConsumerRecord<String, String> record, Acknowledgment acknowledgment) {
        // TODO: implement
        //   1. Deserialise record.value() from JSON
        //   2. Call validate(rawJson)
        //   3. Call normalizationService.normalize(rawPayload)
        //   4. Publish NormalizedEvent to output topic
        //   5. Call acknowledgment.acknowledge()
        //   6. On validation failure: route to dead-letter topic, still ack
        throw new UnsupportedOperationException("TODO: implement consume — REQ-A, REQ-B");
    }

    /**
     * Validates the raw JSON payload against the expected event schema.
     *
     * <p><b>REQ-B</b>: Validation shall reject events missing mandatory fields
     * ({@code event_id}, {@code timestamp}, {@code source}) and log a
     * structured error with the partition and offset.
     *
     * @param rawJson raw event JSON string received from Kafka
     * @return {@code true} if the payload is structurally valid
     * @throws com.eventproc.ingestion.exception.SchemaValidationException
     *         if the payload violates the schema
     */
    public boolean validate(String rawJson) {
        // TODO: implement — REQ-B
        throw new UnsupportedOperationException("TODO: implement validate — REQ-B");
    }

    /**
     * Triggers normalisation of a validated raw payload.
     *
     * <p><b>REQ-B</b>: After validation passes the event is forwarded to
     * {@link NormalizationService#normalize(java.util.Map)} for field mapping.
     *
     * @param rawJson validated raw event JSON string
     * @return {@link NormalizedEvent} produced by the normalisation service
     */
    public NormalizedEvent normalize(String rawJson) {
        // TODO: implement — REQ-B
        throw new UnsupportedOperationException("TODO: implement normalize — REQ-B");
    }
}
