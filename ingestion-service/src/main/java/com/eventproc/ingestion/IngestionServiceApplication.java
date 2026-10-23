package com.eventproc.ingestion;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.kafka.annotation.EnableKafka;

/**
 * Entry-point for the Ingestion Service — synchronous Kafka consumer path.
 *
 * <p>Supports REQ-001 (event ingestion), REQ-005 (validation), REQ-008 (normalisation), REQ-040 (at-least-once idempotent delivery).
 */
@SpringBootApplication
@EnableKafka
public class IngestionServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(IngestionServiceApplication.class, args);
    }
}
