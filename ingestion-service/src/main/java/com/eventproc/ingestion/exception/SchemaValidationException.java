package com.eventproc.ingestion.exception;

import java.util.List;
import java.util.stream.Collectors;

/**
 * Raised when a raw event violates the input contract (docs/CONTRACTS.md §1.1).
 *
 * <p>Carries one {@link FieldError} per violated rule so callers can log and
 * route the rejection with field-level detail. The exception message is a
 * compact, human-readable summary suitable for the {@code x-error-reason}
 * DLT header.
 *
 * <p>Validation failures are deterministic, so this exception is never retried.
 */
public class SchemaValidationException extends RuntimeException {

    /** Pseudo-field used for errors that concern the whole document. */
    public static final String ROOT = "$";

    private final transient List<FieldError> errors;

    public SchemaValidationException(List<FieldError> errors) {
        super(summarize(errors));
        if (errors == null || errors.isEmpty()) {
            throw new IllegalArgumentException("errors must not be empty");
        }
        this.errors = List.copyOf(errors);
    }

    public SchemaValidationException(List<FieldError> errors, Throwable cause) {
        this(errors);
        initCause(cause);
    }

    /** Convenience factory for a single error. */
    public static SchemaValidationException of(String field, String message) {
        return new SchemaValidationException(List.of(new FieldError(field, message)));
    }

    public List<FieldError> getErrors() {
        return errors;
    }

    private static String summarize(List<FieldError> errors) {
        if (errors == null || errors.isEmpty()) {
            return "schema validation failed";
        }
        return "schema validation failed: " + errors.stream()
                .map(FieldError::toString)
                .collect(Collectors.joining("; "));
    }

    /** A single violated rule. */
    public record FieldError(String field, String message) {
        @Override
        public String toString() {
            return field + " " + message;
        }
    }
}
