package com.eventproc.ingestion.service;

import com.eventproc.ingestion.exception.SchemaValidationException;
import com.eventproc.ingestion.exception.SchemaValidationException.FieldError;
import com.eventproc.ingestion.model.NormalizedEvent;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.ObjectReader;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Parses, validates and normalises raw events (docs/CONTRACTS.md §1.1 → §1.2).
 *
 * <p>Pure and stateless apart from configuration: no Kafka, no I/O, so it can be
 * unit tested in isolation. Every rule violation is reported as a
 * {@link SchemaValidationException} carrying all field-level errors found.
 *
 * <p>Requirements: REQ-B (schema validation), REQ-C (normalisation).
 */
@Service
public class NormalizationService {

    public static final String EVENT_ID = "event_id";
    public static final String TIMESTAMP = "timestamp";
    public static final String SOURCE = "source";
    public static final String TYPE = "type";
    public static final String COMPONENT_ID = "component_id";
    public static final String PAYLOAD = "payload";

    public static final String DEFAULT_TYPE = "generic";

    /** Top-level keys allowed by the raw event contract. */
    public static final Set<String> KNOWN_FIELDS =
            Set.of(EVENT_ID, TIMESTAMP, SOURCE, TYPE, COMPONENT_ID, PAYLOAD);

    /** Mirrors contracts/raw-event.schema.json $defs/isoOffsetDateTime (seconds and an explicit offset required). */
    private static final Pattern ISO_OFFSET_PATTERN =
            Pattern.compile("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d{1,9})?(Z|z|[+-]\\d{2}:\\d{2})$");

    private static final TypeReference<LinkedHashMap<String, Object>> MAP_TYPE = new TypeReference<>() {};

    private final ObjectMapper objectMapper;
    private final ObjectReader treeReader;
    private final Clock clock;
    private final String schemaVersion;
    private final boolean strictValidation;

    @Autowired
    public NormalizationService(ObjectMapper objectMapper,
                                Clock clock,
                                @Value("${app.normalization.schema-version:1.0}") String schemaVersion,
                                @Value("${app.normalization.strict-validation:true}") boolean strictValidation) {
        this.objectMapper = objectMapper;
        this.treeReader = objectMapper.reader().with(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
        this.clock = clock;
        this.schemaVersion = schemaVersion;
        this.strictValidation = strictValidation;
    }

    // ── Public API ────────────────────────────────────────────────────────

    /**
     * Full pipeline for one Kafka record value: parse → validate → normalise.
     *
     * @throws SchemaValidationException if the bytes are not a contract-conforming event
     */
    public NormalizedEvent process(byte[] rawValue) {
        return normalize(parse(rawValue));
    }

    /**
     * Parses raw bytes into a top-level JSON object.
     *
     * @throws SchemaValidationException for empty input, malformed JSON or a non-object document
     */
    public Map<String, Object> parse(byte[] rawValue) {
        if (rawValue == null || rawValue.length == 0) {
            throw SchemaValidationException.of(SchemaValidationException.ROOT, "message is empty");
        }
        JsonNode node;
        try {
            node = treeReader.readTree(rawValue);
        } catch (IOException e) {
            String detail = e instanceof JsonProcessingException jpe ? jpe.getOriginalMessage() : e.getMessage();
            throw new SchemaValidationException(
                    List.of(new FieldError(SchemaValidationException.ROOT, "is not valid JSON (" + detail + ")")), e);
        }
        if (node == null || node.isMissingNode()) {
            throw SchemaValidationException.of(SchemaValidationException.ROOT, "message is empty");
        }
        if (!node.isObject()) {
            throw SchemaValidationException.of(SchemaValidationException.ROOT,
                    "must be a JSON object but was " + node.getNodeType().name().toLowerCase());
        }
        return objectMapper.convertValue(node, MAP_TYPE);
    }

    /**
     * Validates a parsed raw event against CONTRACTS §1.1.
     *
     * <p>Rules: {@code event_id} and {@code source} are required non-blank strings;
     * {@code timestamp} is a required ISO-8601 string with seconds and an offset or {@code Z};
     * {@code type} and {@code component_id}, if present, must be strings and {@code payload},
     * if present, must be an object — an explicit JSON {@code null} is invalid, as in
     * contracts/raw-event.schema.json. In strict mode unknown top-level keys are rejected.
     *
     * @return {@code true} when valid
     * @throws SchemaValidationException listing every violation found
     */
    public boolean validate(Map<String, Object> raw) {
        if (raw == null) {
            throw SchemaValidationException.of(SchemaValidationException.ROOT, "must be a JSON object but was null");
        }
        List<FieldError> errors = new ArrayList<>();

        requireNonBlankString(raw, EVENT_ID, errors);
        if (requireNonBlankString(raw, TIMESTAMP, errors)) {
            try {
                parseTimestamp((String) raw.get(TIMESTAMP));
            } catch (DateTimeParseException e) {
                errors.add(new FieldError(TIMESTAMP,
                        "must be an ISO-8601 date-time with offset or Z (e.g. 2026-01-01T12:00:00+02:00)"));
            }
        }
        requireNonBlankString(raw, SOURCE, errors);
        optionalOfType(raw, TYPE, String.class, "must be a string", errors);
        optionalOfType(raw, COMPONENT_ID, String.class, "must be a string", errors);
        optionalOfType(raw, PAYLOAD, Map.class, "must be a JSON object", errors);

        if (strictValidation) {
            raw.keySet().stream()
                    .filter(k -> !KNOWN_FIELDS.contains(k))
                    .sorted()
                    .forEach(k -> errors.add(new FieldError(k, "is not an allowed field (strict validation)")));
        }

        if (!errors.isEmpty()) {
            throw new SchemaValidationException(errors);
        }
        return true;
    }

    /**
     * Validates and maps a raw event to a {@link NormalizedEvent} (CONTRACTS §1.2):
     * string fields are copied verbatim, converts the timestamp to a UTC instant, applies defaults
     * ({@code type="generic"}, {@code component_id=null}, {@code payload={}}) and stamps
     * {@code schema_version} and {@code ingested_at}. Unknown keys (lenient mode) are dropped.
     *
     * @throws SchemaValidationException if the input is invalid
     */
    @SuppressWarnings("unchecked")
    public NormalizedEvent normalize(Map<String, Object> raw) {
        validate(raw);

        String type = (String) raw.get(TYPE);
        Object payload = raw.get(PAYLOAD);

        return new NormalizedEvent(
                (String) raw.get(EVENT_ID),
                parseTimestamp((String) raw.get(TIMESTAMP)),
                (String) raw.get(SOURCE),
                type != null ? type : DEFAULT_TYPE,
                (String) raw.get(COMPONENT_ID),
                payload != null ? new LinkedHashMap<>((Map<String, Object>) payload) : new LinkedHashMap<>(),
                schemaVersion,
                clock.instant().truncatedTo(ChronoUnit.MILLIS));
    }

    public String getSchemaVersion() {
        return schemaVersion;
    }

    public boolean isStrictValidation() {
        return strictValidation;
    }

    // ── Internals ─────────────────────────────────────────────────────────

    private static Instant parseTimestamp(String value) {
        if (!ISO_OFFSET_PATTERN.matcher(value).matches()) {
            throw new DateTimeParseException("not an ISO-8601 date-time with offset", value, 0);
        }
        String canonical = value.endsWith("z") ? value.substring(0, value.length() - 1) + "Z" : value;
        return OffsetDateTime.parse(canonical, DateTimeFormatter.ISO_OFFSET_DATE_TIME).toInstant();
    }

    /** @return true if the field is a non-blank string */
    private static boolean requireNonBlankString(Map<String, Object> raw, String field, List<FieldError> errors) {
        Object v = raw.get(field);
        if (v == null) {
            errors.add(new FieldError(field, "is required"));
            return false;
        }
        if (!(v instanceof String s)) {
            errors.add(new FieldError(field, "must be a string"));
            return false;
        }
        if (s.isBlank()) {
            errors.add(new FieldError(field, "must not be blank"));
            return false;
        }
        return true;
    }

    /** Optional field: may be absent, but if present must be non-null and of the given type. */
    private static void optionalOfType(Map<String, Object> raw, String field, Class<?> type,
                                       String typeMessage, List<FieldError> errors) {
        if (!raw.containsKey(field)) {
            return;
        }
        Object v = raw.get(field);
        if (v == null) {
            errors.add(new FieldError(field, "must not be null (omit the field instead)"));
        } else if (!type.isInstance(v)) {
            errors.add(new FieldError(field, typeMessage));
        }
    }
}
