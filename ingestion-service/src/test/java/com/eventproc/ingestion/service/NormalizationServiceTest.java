package com.eventproc.ingestion.service;

import com.eventproc.ingestion.exception.SchemaValidationException;
import com.eventproc.ingestion.exception.SchemaValidationException.FieldError;
import com.eventproc.ingestion.model.NormalizedEvent;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.catchThrowableOfType;

class NormalizationServiceTest {

    private static final Instant NOW = Instant.parse("2026-01-01T10:00:00.123456789Z");
    private static final ObjectMapper MAPPER = new ObjectMapper()
            .registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);

    private final NormalizationService strict = service(true);
    private final NormalizationService lenient = service(false);

    private static NormalizationService service(boolean strict) {
        return new NormalizationService(MAPPER, Clock.fixed(NOW, ZoneOffset.UTC), "1.0", strict);
    }

    private static Map<String, Object> valid() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("event_id", "e-123");
        m.put("timestamp", "2026-01-01T12:00:00+02:00");
        m.put("source", "checkout-api");
        m.put("type", "http_request");
        m.put("component_id", "ingestion-service");
        m.put("payload", Map.of("status", 500));
        return m;
    }

    private static byte[] bytes(String s) {
        return s.getBytes(StandardCharsets.UTF_8);
    }

    private static List<FieldError> errorsOf(Runnable r) {
        SchemaValidationException e = catchThrowableOfType(r::run, SchemaValidationException.class);
        assertThat(e).as("expected SchemaValidationException").isNotNull();
        return e.getErrors();
    }

    // ── normalisation ─────────────────────────────────────────────────────

    @Test
    void normalizesContractExample() throws Exception {
        NormalizedEvent ev = strict.normalize(valid());

        assertThat(ev.getEventId()).isEqualTo("e-123");
        assertThat(ev.getTimestamp()).isEqualTo(Instant.parse("2026-01-01T10:00:00Z"));
        assertThat(ev.getSource()).isEqualTo("checkout-api");
        assertThat(ev.getType()).isEqualTo("http_request");
        assertThat(ev.getComponentId()).isEqualTo("ingestion-service");
        assertThat(ev.getPayload()).containsEntry("status", 500);
        assertThat(ev.getSchemaVersion()).isEqualTo("1.0");
        assertThat(ev.getIngestedAt()).isEqualTo(Instant.parse("2026-01-01T10:00:00.123Z"));

        JsonNode json = MAPPER.readTree(MAPPER.writeValueAsBytes(ev));
        assertThat(json.get("timestamp").asText()).isEqualTo("2026-01-01T10:00:00Z");
        assertThat(json.get("ingested_at").asText()).isEqualTo("2026-01-01T10:00:00.123Z");
        assertThat(json.get("schema_version").asText()).isEqualTo("1.0");
        assertThat(json.get("payload").get("status").asInt()).isEqualTo(500);
        assertThat(json.fieldNames()).toIterable().containsExactly(
                "event_id", "timestamp", "source", "type", "component_id", "payload", "schema_version", "ingested_at");
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "2026-01-01T10:00:00Z",
            "2026-01-01T12:00:00+02:00",
            "2026-01-01T05:00:00-05:00",
            "2026-01-01T15:30:00+05:30",
            "2025-12-31T23:00:00-11:00"})
    void convertsOffsetsToUtc(String ts) {
        Map<String, Object> raw = valid();
        raw.put("timestamp", ts);
        assertThat(strict.normalize(raw).getTimestamp()).isEqualTo(Instant.parse("2026-01-01T10:00:00Z"));
    }

    @Test
    void keepsFractionalSeconds() {
        Map<String, Object> raw = valid();
        raw.put("timestamp", "2026-01-01T12:00:00.987+02:00");
        assertThat(strict.normalize(raw).getTimestamp()).isEqualTo(Instant.parse("2026-01-01T10:00:00.987Z"));
    }

    @Test
    void appliesDefaultsForAbsentOptionalFields() throws Exception {
        Map<String, Object> raw = new LinkedHashMap<>();
        raw.put("event_id", "e-1");
        raw.put("timestamp", "2026-01-01T10:00:00Z");
        raw.put("source", "svc");

        NormalizedEvent ev = strict.normalize(raw);

        assertThat(ev.getType()).isEqualTo("generic");
        assertThat(ev.getComponentId()).isNull();
        assertThat(ev.getPayload()).isEmpty();
        JsonNode json = MAPPER.readTree(MAPPER.writeValueAsBytes(ev));
        assertThat(json.has("component_id")).isTrue();
        assertThat(json.get("component_id").isNull()).isTrue();
        assertThat(json.get("payload").isObject()).isTrue();
    }

    @ParameterizedTest
    @ValueSource(strings = {"type", "component_id", "payload"})
    void rejectsExplicitNullOptionalField(String field) {
        Map<String, Object> raw = valid();
        raw.put(field, null);
        assertThat(errorsOf(() -> strict.validate(raw)))
                .containsExactly(new FieldError(field, "must not be null (omit the field instead)"));
        assertThat(errorsOf(() -> lenient.validate(raw))).hasSize(1);
    }

    @Test
    void rejectsExplicitNullOptionalFieldFromJson() {
        List<FieldError> errors = errorsOf(() -> strict.process(bytes(
                "{\"event_id\":\"e\",\"timestamp\":\"2026-01-01T10:00:00Z\",\"source\":\"s\",\"payload\":null}")));
        assertThat(errors).extracting(FieldError::field).containsExactly("payload");
    }

    @Test
    void copiesStringFieldsVerbatim() {
        Map<String, Object> raw = valid();
        raw.put("event_id", " e-9 ");
        raw.put("source", " svc");
        raw.put("type", "");
        raw.put("component_id", "");
        NormalizedEvent ev = strict.normalize(raw);
        assertThat(ev.getEventId()).isEqualTo(" e-9 ");
        assertThat(ev.getSource()).isEqualTo(" svc");
        assertThat(ev.getType()).isEmpty();
        assertThat(ev.getComponentId()).isEmpty();
    }

    @Test
    void acceptsLowercaseZulu() {
        Map<String, Object> raw = valid();
        raw.put("timestamp", "2026-01-01T10:00:00.5z");
        assertThat(strict.normalize(raw).getTimestamp()).isEqualTo(Instant.parse("2026-01-01T10:00:00.500Z"));
    }

    @Test
    void processParsesBytesEndToEnd() {
        NormalizedEvent ev = strict.process(bytes(
                "{\"event_id\":\"e-1\",\"timestamp\":\"2026-01-01T10:00:00Z\",\"source\":\"s\",\"payload\":{\"a\":[1,2]}}"));
        assertThat(ev.getEventId()).isEqualTo("e-1");
        assertThat(ev.getPayload()).containsEntry("a", List.of(1, 2));
    }

    // ── required fields ───────────────────────────────────────────────────

    @ParameterizedTest
    @ValueSource(strings = {"event_id", "timestamp", "source"})
    void rejectsMissingRequiredField(String field) {
        Map<String, Object> raw = valid();
        raw.remove(field);
        assertThat(errorsOf(() -> strict.validate(raw)))
                .containsExactly(new FieldError(field, "is required"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"event_id", "timestamp", "source"})
    void rejectsNullRequiredField(String field) {
        Map<String, Object> raw = valid();
        raw.put(field, null);
        assertThat(errorsOf(() -> strict.validate(raw)))
                .containsExactly(new FieldError(field, "is required"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"event_id", "timestamp", "source"})
    void rejectsBlankRequiredField(String field) {
        Map<String, Object> raw = valid();
        raw.put(field, "   ");
        assertThat(errorsOf(() -> strict.validate(raw)))
                .containsExactly(new FieldError(field, "must not be blank"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"event_id", "timestamp", "source"})
    void rejectsNonStringRequiredField(String field) {
        Map<String, Object> raw = valid();
        raw.put(field, 42);
        assertThat(errorsOf(() -> strict.validate(raw)))
                .containsExactly(new FieldError(field, "must be a string"));
    }

    @Test
    void reportsAllErrorsAtOnce() {
        Map<String, Object> raw = new LinkedHashMap<>();
        raw.put("type", 7);
        raw.put("payload", "nope");
        List<FieldError> errors = errorsOf(() -> strict.validate(raw));
        assertThat(errors).extracting(FieldError::field)
                .containsExactly("event_id", "timestamp", "source", "type", "payload");
    }

    @Test
    void exceptionMessageSummarizesErrors() {
        Map<String, Object> raw = valid();
        raw.remove("source");
        assertThatThrownBy(() -> strict.normalize(raw))
                .isInstanceOf(SchemaValidationException.class)
                .hasMessage("schema validation failed: source is required");
    }

    // ── timestamp ─────────────────────────────────────────────────────────

    @ParameterizedTest
    @ValueSource(strings = {
            "2026-01-01T10:00:00",        // no offset
            "2026-01-01",                 // date only
            "01/01/2026 10:00",           // not ISO
            "1767261600",                 // epoch seconds as string
            "2026-13-01T10:00:00Z",       // invalid month
            "2026-01-01T25:00:00Z",       // invalid hour
            "2026-01-01T10:00Z",          // seconds missing
            "2026-01-01T10:00:00+0200",   // offset without colon
            " 2026-01-01T10:00:00Z",      // surrounding whitespace
            "not-a-timestamp"})
    void rejectsBadTimestamp(String ts) {
        Map<String, Object> raw = valid();
        raw.put("timestamp", ts);
        List<FieldError> errors = errorsOf(() -> strict.validate(raw));
        assertThat(errors).hasSize(1);
        assertThat(errors.get(0).field()).isEqualTo("timestamp");
        assertThat(errors.get(0).message()).contains("ISO-8601");
    }

    // ── optional field types ──────────────────────────────────────────────

    @Test
    void rejectsWronglyTypedOptionalFields() {
        Map<String, Object> raw = valid();
        raw.put("type", 1);
        raw.put("component_id", List.of("x"));
        raw.put("payload", List.of(1, 2));
        assertThat(errorsOf(() -> strict.validate(raw))).containsExactly(
                new FieldError("type", "must be a string"),
                new FieldError("component_id", "must be a string"),
                new FieldError("payload", "must be a JSON object"));
    }

    // ── strict vs lenient ─────────────────────────────────────────────────

    @Test
    void strictModeRejectsUnknownTopLevelKeys() {
        Map<String, Object> raw = valid();
        raw.put("zeta", 1);
        raw.put("alpha", "x");
        assertThat(errorsOf(() -> strict.validate(raw))).containsExactly(
                new FieldError("alpha", "is not an allowed field (strict validation)"),
                new FieldError("zeta", "is not an allowed field (strict validation)"));
    }

    @Test
    void strictModeAllowsUnknownKeysInsidePayload() {
        Map<String, Object> raw = valid();
        raw.put("payload", Map.of("anything", Map.of("nested", true)));
        assertThat(strict.validate(raw)).isTrue();
    }

    @Test
    void lenientModeAcceptsAndDropsUnknownKeys() throws Exception {
        Map<String, Object> raw = valid();
        raw.put("extra", "x");
        assertThat(lenient.validate(raw)).isTrue();
        NormalizedEvent ev = lenient.normalize(raw);
        JsonNode json = MAPPER.readTree(MAPPER.writeValueAsBytes(ev));
        assertThat(json.has("extra")).isFalse();
    }

    @Test
    void lenientModeStillEnforcesRequiredFields() {
        Map<String, Object> raw = valid();
        raw.put("extra", "x");
        raw.remove("event_id");
        assertThat(errorsOf(() -> lenient.validate(raw)))
                .containsExactly(new FieldError("event_id", "is required"));
    }

    // ── parsing ───────────────────────────────────────────────────────────

    @ParameterizedTest
    @ValueSource(strings = {"[1,2,3]", "\"a string\"", "42", "true", "null"})
    void rejectsNonObjectJson(String json) {
        List<FieldError> errors = errorsOf(() -> strict.process(bytes(json)));
        assertThat(errors).hasSize(1);
        assertThat(errors.get(0).field()).isEqualTo("$");
        assertThat(errors.get(0).message()).startsWith("must be a JSON object");
    }

    @ParameterizedTest
    @ValueSource(strings = {"{not json", "{\"event_id\":\"e\",}", "hello world", "{\"a\":1} trailing"})
    void rejectsMalformedJson(String json) {
        List<FieldError> errors = errorsOf(() -> strict.process(bytes(json)));
        assertThat(errors).hasSize(1);
        assertThat(errors.get(0).field()).isEqualTo("$");
        assertThat(errors.get(0).message()).startsWith("is not valid JSON");
    }

    @Test
    void rejectsEmptyAndNullValues() {
        assertThat(errorsOf(() -> strict.process(new byte[0])))
                .containsExactly(new FieldError("$", "message is empty"));
        assertThat(errorsOf(() -> strict.process(null)))
                .containsExactly(new FieldError("$", "message is empty"));
        assertThat(errorsOf(() -> strict.process(bytes("   "))))
                .containsExactly(new FieldError("$", "message is empty"));
    }

    @Test
    void rejectsNullMap() {
        assertThatThrownBy(() -> strict.validate(null)).isInstanceOf(SchemaValidationException.class);
    }

    @Nested
    class ExceptionContract {
        @Test
        void requiresAtLeastOneError() {
            assertThatThrownBy(() -> new SchemaValidationException(List.of()))
                    .isInstanceOf(IllegalArgumentException.class);
        }

        @Test
        void errorsAreImmutable() {
            SchemaValidationException e = SchemaValidationException.of("f", "bad");
            assertThatThrownBy(() -> e.getErrors().add(new FieldError("x", "y")))
                    .isInstanceOf(UnsupportedOperationException.class);
        }
    }
}
