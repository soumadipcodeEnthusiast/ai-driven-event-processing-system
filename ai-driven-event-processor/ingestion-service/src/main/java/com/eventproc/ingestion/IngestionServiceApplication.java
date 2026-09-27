package com.eventproc.ingestion;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.kafka.annotation.EnableKafka;

/**
 * Entry-point for the Ingestion Service — synchronous Kafka consumer path.
 *
 * <p>Supports REQ-A (event ingestion), REQ-B (validation), REQ-C (normalisation).
 */
@SpringBootApplication
@EnableKafka
public class IngestionServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(IngestionServiceApplication.class, args);
    }
}
