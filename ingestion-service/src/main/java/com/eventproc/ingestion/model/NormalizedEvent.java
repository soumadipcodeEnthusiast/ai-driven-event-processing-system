package com.eventproc.ingestion.model;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

import java.time.Instant;
import java.util.Map;
import java.util.Objects;

/**
 * Domain model representing a validated, normalised event ready for downstream
 * processing.
 *
 * <p>Attributes sourced from the class diagram:
 * <ul>
 *   <li>{@code eventId}      — globally unique event identifier</li>
 *   <li>{@code timestamp}    — event occurrence time (UTC)</li>
 *   <li>{@code payload}      — normalised key-value payload</li>
 *   <li>{@code schemaVersion}— version of the normalisation schema applied</li>
 * </ul>
 */
public class NormalizedEvent {

    /** Globally unique event identifier. */
    @NotBlank
    @JsonProperty("event_id")
    private String eventId;

    /** UTC instant at which the originating event occurred. */
    @NotNull
    @JsonProperty("timestamp")
    private Instant timestamp;

    /** Normalised payload key-value map. */
    @NotNull
    @JsonProperty("payload")
    private Map<String, Object> payload;

    /** Schema version applied during normalisation (e.g. "1.0"). */
    @NotBlank
    @JsonProperty("schema_version")
    private String schemaVersion;

    // ── Constructors ──────────────────────────────────────────────────────

    public NormalizedEvent() {}

    public NormalizedEvent(String eventId,
                           Instant timestamp,
                           Map<String, Object> payload,
                           String schemaVersion) {
        this.eventId = eventId;
        this.timestamp = timestamp;
        this.payload = payload;
        this.schemaVersion = schemaVersion;
    }

    // ── Getters / Setters ─────────────────────────────────────────────────

    public String getEventId() {
        return eventId;
    }

    public void setEventId(String eventId) {
        this.eventId = eventId;
    }

    public Instant getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(Instant timestamp) {
        this.timestamp = timestamp;
    }

    public Map<String, Object> getPayload() {
        return payload;
    }

    public void setPayload(Map<String, Object> payload) {
        this.payload = payload;
    }

    public String getSchemaVersion() {
        return schemaVersion;
    }

    public void setSchemaVersion(String schemaVersion) {
        this.schemaVersion = schemaVersion;
    }

    // ── Object overrides ──────────────────────────────────────────────────

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof NormalizedEvent that)) return false;
        return Objects.equals(eventId, that.eventId);
    }

    @Override
    public int hashCode() {
        return Objects.hash(eventId);
    }

    @Override
    public String toString() {
        return "NormalizedEvent{" +
               "eventId='" + eventId + '\'' +
               ", timestamp=" + timestamp +
               ", schemaVersion='" + schemaVersion + '\'' +
               '}';
    }
}
