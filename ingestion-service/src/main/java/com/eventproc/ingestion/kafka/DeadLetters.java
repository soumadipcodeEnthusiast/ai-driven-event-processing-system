package com.eventproc.ingestion.kafka;

import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.header.Headers;
import org.apache.kafka.common.header.internals.RecordHeaders;
import org.springframework.core.NestedExceptionUtils;
import org.springframework.kafka.listener.BatchListenerFailedException;
import org.springframework.kafka.listener.ListenerExecutionFailedException;

import java.nio.charset.StandardCharsets;

/**
 * Builds dead-letter records per docs/CONTRACTS.md §1: original key, original raw
 * bytes unchanged, and the {@code x-error-reason} / {@code x-original-*} headers.
 * Used both for validation rejections and by the error handler's recoverer, so both
 * paths produce identical DLT records.
 */
public final class DeadLetters {

    public static final String ERROR_REASON = "x-error-reason";
    public static final String ORIGINAL_TOPIC = "x-original-topic";
    public static final String ORIGINAL_PARTITION = "x-original-partition";
    public static final String ORIGINAL_OFFSET = "x-original-offset";

    private DeadLetters() {}

    public static Headers headers(ConsumerRecord<?, ?> source, String reason) {
        Headers h = new RecordHeaders();
        h.add(ERROR_REASON, utf8(reason == null ? "unknown" : reason));
        h.add(ORIGINAL_TOPIC, utf8(source.topic()));
        h.add(ORIGINAL_PARTITION, utf8(Integer.toString(source.partition())));
        h.add(ORIGINAL_OFFSET, utf8(Long.toString(source.offset())));
        return h;
    }

    public static ProducerRecord<String, byte[]> record(String dltTopic,
                                                        ConsumerRecord<String, byte[]> source,
                                                        String reason) {
        return new ProducerRecord<>(dltTopic, null, source.key(), source.value(), headers(source, reason));
    }

    /** Human-readable reason for an unexpected failure, unwrapping Spring Kafka wrappers. */
    public static String reason(Throwable ex) {
        Throwable t = ex;
        while ((t instanceof ListenerExecutionFailedException || t instanceof BatchListenerFailedException)
                && t.getCause() != null) {
            t = t.getCause();
        }
        Throwable root = NestedExceptionUtils.getMostSpecificCause(t);
        String msg = root.getMessage();
        return root.getClass().getSimpleName() + (msg == null ? "" : ": " + msg);
    }

    private static byte[] utf8(String s) {
        return s.getBytes(StandardCharsets.UTF_8);
    }
}
