package com.eventproc.ingestion.metrics;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

/**
 * Ingestion metrics (docs/CONTRACTS.md §7). Names are fixed:
 * <ul>
 *   <li>{@code ingestion.events{outcome=valid|invalid|error}} → Prometheus {@code ingestion_events_total}</li>
 *   <li>{@code ingestion.processing} timer with histogram → {@code ingestion_processing_seconds_*}</li>
 * </ul>
 * All outcome series are registered eagerly so they are exported as 0 before the first event.
 */
@Component
public class IngestionMetrics {

    public static final String EVENTS = "ingestion.events";
    public static final String PROCESSING = "ingestion.processing";

    private final MeterRegistry registry;
    private final Counter valid;
    private final Counter invalid;
    private final Counter error;
    private final Timer processing;

    public IngestionMetrics(MeterRegistry registry) {
        this.registry = registry;
        this.valid = outcome("valid");
        this.invalid = outcome("invalid");
        this.error = outcome("error");
        this.processing = Timer.builder(PROCESSING)
                .description("Time to parse, validate, normalise and hand off one raw event")
                .publishPercentileHistogram()
                .register(registry);
    }

    private Counter outcome(String outcome) {
        return Counter.builder(EVENTS)
                .description("Raw events processed by outcome")
                .tag("outcome", outcome)
                .register(registry);
    }

    public void valid() { valid.increment(); }

    public void invalid() { invalid.increment(); }

    public void error() { error.increment(); }

    public Timer.Sample startTimer() {
        return Timer.start(registry);
    }

    public void stopTimer(Timer.Sample sample) {
        sample.stop(processing);
    }
}
