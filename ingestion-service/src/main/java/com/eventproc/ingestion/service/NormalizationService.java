package com.eventproc.ingestion.service;

import com.eventproc.ingestion.model.NormalizedEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.Map;

/**
 * Service responsible for validating and normalising raw event payloads
 * into {@link NormalizedEvent} instances.
 *
 * <p>Implements the {@code IngestionService} operations from the class diagram.
 *
 * <p>Requirements covered:
 * <ul>
 *   <li>REQ-B — Schema validation</li>
 *   <li>REQ-C — Field normalisation and schema-version stamping</li>
 * </ul>
 */
@Service
public class NormalizationService {

    private static final Logger log = LoggerFactory.getLogger(NormalizationService.class);

    /** Schema version to stamp on every produced {@link NormalizedEvent}. */
    @Value("${app.normalization.schema-version:1.0}")
    private String schemaVersion;

    /** When {@code true}, unknown fields cause validation failure. */
    @Value("${app.normalization.strict-validation:true}")
    private boolean strictValidation;

    // ── Public API ────────────────────────────────────────────────────────

    /**
     * Validates that the raw payload map contains all mandatory fields and
     * that field types conform to the registered schema.
     *
     * <p><b>REQ-B</b>: The system shall reject events that are missing
     * {@code event_id}, {@code timestamp}, or {@code source} fields and
     * shall record a structured validation error for each rejection.
     *
     * @param rawPayload key-value map deserialised from the raw Kafka message
     * @return {@code true} if all mandatory fields are present and well-typed
     * @throws com.eventproc.ingestion.exception.SchemaValidationException
     *         if any mandatory field is absent or malformed
     */
    public boolean validate(Map<String, Object> rawPayload) {
        // TODO: implement — REQ-B
        //   1. Check mandatory keys: event_id, timestamp, source
        //   2. If strictValidation, reject unknown keys
        //   3. Validate timestamp parses as ISO-8601
        //   4. Throw SchemaValidationException with field-level detail on failure
        throw new UnsupportedOperationException("TODO: implement validate — REQ-B");
    }

    /**
     * Maps a validated raw payload to a {@link NormalizedEvent}, applying
     * field renaming, type coercion, and schema-version stamping.
     *
     * <p><b>REQ-C</b>: The system shall produce a {@link NormalizedEvent}
     * whose {@code schemaVersion} matches the currently configured version
     * and whose {@code timestamp} is stored as UTC {@link java.time.Instant}.
     *
     * @param rawPayload validated key-value map from the raw Kafka message
     * @return a fully populated {@link NormalizedEvent}
     */
    public NormalizedEvent normalize(Map<String, Object> rawPayload) {
        // TODO: implement — REQ-C
        //   1. Extract and cast eventId (String)
        //   2. Parse timestamp string → Instant (UTC)
        //   3. Build normalised payload map (rename / drop fields per schema)
        //   4. Stamp schemaVersion from configuration
        //   5. Return new NormalizedEvent(eventId, timestamp, payload, schemaVersion)
        throw new UnsupportedOperationException("TODO: implement normalize — REQ-C");
    }

    // ── Accessors (for testing) ───────────────────────────────────────────

    public String getSchemaVersion() {
        return schemaVersion;
    }

    public boolean isStrictValidation() {
        return strictValidation;
    }
}
