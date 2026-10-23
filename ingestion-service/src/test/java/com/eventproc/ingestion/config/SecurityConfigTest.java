package com.eventproc.ingestion.config;

import org.junit.jupiter.api.Test;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtException;

import java.time.Instant;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** REQ-046: token validation rules of {@link SecurityConfig}. */
class SecurityConfigTest {

    private static final String ISSUER = "https://idp.example.com";

    private static Jwt jwt(String issuer, List<String> audience, Instant expiresAt) {
        return Jwt.withTokenValue("t")
                .header("alg", "RS256")
                .claim("iss", issuer)
                .audience(audience)
                .issuedAt(expiresAt.minusSeconds(600))
                .expiresAt(expiresAt)
                .build();
    }

    @Test
    void acceptsMatchingIssuerAndAudience() {
        OAuth2TokenValidator<Jwt> v = SecurityConfig.validator(ISSUER, "event-processor");
        Jwt ok = jwt(ISSUER, List.of("event-processor"), Instant.now().plusSeconds(300));
        assertThat(v.validate(ok).hasErrors()).isFalse();
    }

    @Test
    void rejectsWrongAudienceIssuerOrExpiredToken() {
        OAuth2TokenValidator<Jwt> v = SecurityConfig.validator(ISSUER, "event-processor");
        Instant later = Instant.now().plusSeconds(300);
        assertThat(v.validate(jwt(ISSUER, List.of("other"), later)).hasErrors()).isTrue();
        assertThat(v.validate(jwt("https://evil.example.com", List.of("event-processor"), later))
                .hasErrors()).isTrue();
        assertThat(v.validate(jwt(ISSUER, List.of("event-processor"), Instant.now().minusSeconds(3600)))
                .hasErrors()).isTrue();
    }

    @Test
    void audienceCheckIsOptional() {
        OAuth2TokenValidator<Jwt> v = SecurityConfig.validator(ISSUER, "");
        assertThat(v.validate(jwt(ISSUER, List.of("anything"), Instant.now().plusSeconds(300)))
                .hasErrors()).isFalse();
    }

    @Test
    void unconfiguredDecoderDeniesEverything() {
        JwtDecoder decoder = new SecurityConfig().jwtDecoder("", "", "");
        assertThatThrownBy(() -> decoder.decode("any.token.value")).isInstanceOf(JwtException.class);
    }

    @Test
    void issuerOnlyDecoderIsLazy() {
        // Must not contact the issuer at construction time (startup can't depend on the IdP).
        assertThat(new SecurityConfig().jwtDecoder("https://unreachable.invalid", "", "")).isNotNull();
    }
}
