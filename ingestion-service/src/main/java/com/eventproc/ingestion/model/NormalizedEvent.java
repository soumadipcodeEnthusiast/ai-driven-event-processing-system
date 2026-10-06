package com.eventproc.ingestion.model;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonPropertyOrder;

import java.time.Instant;
import java.util.Map;
import java.util.Objects;

/**
 * Validated, normalised event published to {@code normalized-events}
 * (docs/CONTRACTS.md §1.2).
 *
 * <p>JSON field names are snake_case; instants are serialised as ISO-8601 UTC
 * strings. {@code component_id} is always present (possibly {@code null}).
 */
@JsonInclude(JsonInclude.Include.ALWAYS)
@JsonPropertyOrder({"event_id", "timestamp", "source", "type", "component_id",
        "payload", "schema_version", "ingested_at"})
public class NormalizedEvent {

    @JsonProperty("event_id")
    private String eventId;

    /** Event occurrence time, converted to UTC. */
    @JsonProperty("timestamp")
    @JsonFormat(shape = JsonFormat.Shape.STRING)
    private Instant timestamp;

    @JsonProperty("source")
    private String source;

    /** Defaults to {@code "generic"}. */
    @JsonProperty("type")
    private String type;

    /** Nullable. */
    @JsonProperty("component_id")
    private String componentId;

    /** Defaults to an empty object. */
    @JsonProperty("payload")
    private Map<String, Object> payload;

    @JsonProperty("schema_version")
    private String schemaVersion;

    /** Time the ingestion service normalised the event (UTC, millisecond precision). */
    @JsonProperty("ingested_at")
    @JsonFormat(shape = JsonFormat.Shape.STRING)
    private Instant ingestedAt;

    public NormalizedEvent() {}

    public NormalizedEvent(String eventId,
                           Instant timestamp,
                           String source,
                           String type,
                           String componentId,
                           Map<String, Object> payload,
                           String schemaVersion,
                           Instant ingestedAt) {
        this.eventId = eventId;
        this.timestamp = timestamp;
        this.source = source;
        this.type = type;
        this.componentId = componentId;
        this.payload = payload;
        this.schemaVersion = schemaVersion;
        this.ingestedAt = ingestedAt;
    }

    public String getEventId() { return eventId; }
    public void setEventId(String eventId) { this.eventId = eventId; }

    public Instant getTimestamp() { return timestamp; }
    public void setTimestamp(Instant timestamp) { this.timestamp = timestamp; }

    public String getSource() { return source; }
    public void setSource(String source) { this.source = source; }

    public String getType() { return type; }
    public void setType(String type) { this.type = type; }

    public String getComponentId() { return componentId; }
    public void setComponentId(String componentId) { this.componentId = componentId; }

    public Map<String, Object> getPayload() { return payload; }
    public void setPayload(Map<String, Object> payload) { this.payload = payload; }

    public String getSchemaVersion() { return schemaVersion; }
    public void setSchemaVersion(String schemaVersion) { this.schemaVersion = schemaVersion; }

    public Instant getIngestedAt() { return ingestedAt; }
    public void setIngestedAt(Instant ingestedAt) { this.ingestedAt = ingestedAt; }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof NormalizedEvent that)) return false;
        return Objects.equals(eventId, that.eventId)
                && Objects.equals(timestamp, that.timestamp)
                && Objects.equals(source, that.source)
                && Objects.equals(type, that.type)
                && Objects.equals(componentId, that.componentId)
                && Objects.equals(payload, that.payload)
                && Objects.equals(schemaVersion, that.schemaVersion)
                && Objects.equals(ingestedAt, that.ingestedAt);
    }

    @Override
    public int hashCode() {
        return Objects.hash(eventId, timestamp, source, type, componentId, payload, schemaVersion, ingestedAt);
    }

    @Override
    public String toString() {
        return "NormalizedEvent{eventId='" + eventId + "', timestamp=" + timestamp
                + ", source='" + source + "', type='" + type + "', componentId='" + componentId
                + "', schemaVersion='" + schemaVersion + "', ingestedAt=" + ingestedAt + '}';
    }
}
