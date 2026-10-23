package com.eventproc.ingestion.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtDecoders;
import org.springframework.security.oauth2.jwt.JwtException;
import org.springframework.security.oauth2.jwt.JwtValidators;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.jwt.SupplierJwtDecoder;
import org.springframework.security.web.SecurityFilterChain;

import java.util.ArrayList;
import java.util.List;

/**
 * SecurityConfig — OAuth 2.0 resource-server wiring (REQ-046) and the basis
 * for the TLS 1.2+ transport requirement (REQ-047, see application.yml
 * {@code server.ssl}).
 *
 * <p>Actuator health/liveness/readiness probes and the Prometheus scrape stay
 * unauthenticated (k8s and Docker healthchecks don't carry a bearer token);
 * every other endpoint requires a valid JWT.
 *
 * <p>Token validation ({@link #jwtDecoder}) is configured from
 * {@code app.security.oauth2.*}:
 * <ul>
 *   <li>{@code jwk-set-uri} — verify signatures against this JWKS
 *       (e.g. Amazon Cognito's {@code .../.well-known/jwks.json});</li>
 *   <li>{@code issuer-uri} — required {@code iss}; when no JWKS URI is set, the
 *       keys are discovered from the issuer's OpenID configuration on first use
 *       (lazily, so startup doesn't depend on the identity provider);</li>
 *   <li>{@code audience} — when set, the token's {@code aud} must contain it.</li>
 * </ul>
 * With neither URI configured every token is rejected (deny by default).
 */
@Configuration
public class SecurityConfig {

    private static final Logger log = LoggerFactory.getLogger(SecurityConfig.class);

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http, JwtDecoder jwtDecoder) throws Exception {
        return http.sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .csrf(c -> c.disable())
                .authorizeHttpRequests(
                        auth ->
                                auth.requestMatchers(
                                                "/actuator/health/**",
                                                "/actuator/prometheus")
                                        .permitAll()
                                        .anyRequest()
                                        .authenticated())
                .oauth2ResourceServer(oauth2 -> oauth2.jwt(jwt -> jwt.decoder(jwtDecoder)))
                .build();
    }

    @Bean
    public JwtDecoder jwtDecoder(
            @Value("${app.security.oauth2.issuer-uri:}") String issuerUri,
            @Value("${app.security.oauth2.jwk-set-uri:}") String jwkSetUri,
            @Value("${app.security.oauth2.audience:}") String audience) {
        if (jwkSetUri.isBlank() && issuerUri.isBlank()) {
            log.warn("OAuth2 issuer/JWKS not configured: all bearer tokens will be rejected (REQ-046)");
            return token -> {
                throw new JwtException("no OAuth2 issuer or JWKS configured");
            };
        }
        OAuth2TokenValidator<Jwt> validator = validator(issuerUri, audience);
        if (!jwkSetUri.isBlank()) {
            NimbusJwtDecoder decoder = NimbusJwtDecoder.withJwkSetUri(jwkSetUri).build();
            decoder.setJwtValidator(validator);
            return decoder;
        }
        return new SupplierJwtDecoder(() -> {
            NimbusJwtDecoder decoder = JwtDecoders.fromIssuerLocation(issuerUri);
            decoder.setJwtValidator(validator);
            return decoder;
        });
    }

    /** Timestamps (+ issuer when set) + audience (when set). */
    static OAuth2TokenValidator<Jwt> validator(String issuerUri, String audience) {
        List<OAuth2TokenValidator<Jwt>> validators = new ArrayList<>();
        validators.add(issuerUri.isBlank()
                ? JwtValidators.createDefault()
                : JwtValidators.createDefaultWithIssuer(issuerUri));
        if (!audience.isBlank()) {
            validators.add(jwt -> jwt.getAudience() != null && jwt.getAudience().contains(audience)
                    ? OAuth2TokenValidatorResult.success()
                    : OAuth2TokenValidatorResult.failure(
                            new OAuth2Error("invalid_token", "required audience missing", null)));
        }
        return new DelegatingOAuth2TokenValidator<>(validators);
    }
}
