package com.eventproc.ingestion.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

import java.time.Duration;

/**
 * Application-level Kafka settings bound from {@code app.kafka.*}.
 *
 * @param inputTopic      raw events topic ({@code KAFKA_INPUT_TOPIC})
 * @param outputTopic     normalised events topic ({@code KAFKA_OUTPUT_TOPIC})
 * @param deadLetterTopic dead-letter topic ({@code KAFKA_DLT_TOPIC})
 * @param sendTimeout     max time to wait for broker acknowledgement of produced records
 * @param retry           back-off for unexpected (non-validation) errors
 */
@ConfigurationProperties(prefix = "app.kafka")
public record IngestionKafkaProperties(
        @DefaultValue("raw-events") String inputTopic,
        @DefaultValue("normalized-events") String outputTopic,
        @DefaultValue("raw-events.DLT") String deadLetterTopic,
        @DefaultValue("30s") Duration sendTimeout,
        @DefaultValue Retry retry) {

    /**
     * @param maxRetries      retries after the first failed attempt before dead-lettering
     * @param initialInterval first back-off interval
     * @param multiplier      exponential multiplier
     * @param maxInterval     back-off cap
     */
    public record Retry(
            @DefaultValue("3") int maxRetries,
            @DefaultValue("500ms") Duration initialInterval,
            @DefaultValue("2.0") double multiplier,
            @DefaultValue("5s") Duration maxInterval) {
    }
}
