package com.eventproc.ingestion.config;

import com.eventproc.ingestion.kafka.DeadLetters;
import com.eventproc.ingestion.metrics.IngestionMetrics;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.serialization.ByteArrayDeserializer;
import org.apache.kafka.common.serialization.ByteArraySerializer;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.kafka.DefaultKafkaConsumerFactoryCustomizer;
import org.springframework.boot.autoconfigure.kafka.DefaultKafkaProducerFactoryCustomizer;
import org.springframework.boot.autoconfigure.kafka.KafkaProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.ssl.SslBundles;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.ConcurrentKafkaListenerContainerFactory;
import org.springframework.kafka.core.ConsumerFactory;
import org.springframework.kafka.core.DefaultKafkaConsumerFactory;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.core.ProducerFactory;
import org.springframework.kafka.listener.ContainerProperties;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.kafka.support.ExponentialBackOffWithMaxRetries;

import java.time.Clock;
import java.util.Map;

/**
 * Kafka wiring for the ingestion pipeline.
 *
 * <ul>
 *   <li>Record values are handled as raw {@code byte[]} end to end so that dead-lettered
 *       records carry the <b>original bytes unchanged</b> (CONTRACTS §1); keys are strings.</li>
 *   <li>Listener: batch mode, {@code MANUAL_IMMEDIATE} ack, offsets committed only after every
 *       produced record in the batch is acknowledged by the broker (at-least-once).</li>
 *   <li>Unexpected errors: {@link DefaultErrorHandler} retries the failed record with exponential
 *       back-off ({@code app.kafka.retry.*}, default 3 retries), then a
 *       {@link DeadLetterPublishingRecoverer} publishes it to the DLT with the contract headers.</li>
 * </ul>
 */
@Configuration
@EnableConfigurationProperties(IngestionKafkaProperties.class)
public class KafkaConfig {

    private static final Logger log = LoggerFactory.getLogger(KafkaConfig.class);

    @Bean
    @ConditionalOnMissingBean
    public Clock clock() {
        return Clock.systemUTC();
    }

    @Bean
    public ProducerFactory<String, byte[]> producerFactory(KafkaProperties kafkaProperties,
                                                          ObjectProvider<SslBundles> sslBundles,
                                                          ObjectProvider<DefaultKafkaProducerFactoryCustomizer> customizers) {
        Map<String, Object> props = kafkaProperties.buildProducerProperties(sslBundles.getIfAvailable());
        props.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        props.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, ByteArraySerializer.class);
        props.putIfAbsent(ProducerConfig.ACKS_CONFIG, "all");
        props.putIfAbsent(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);
        DefaultKafkaProducerFactory<String, byte[]> factory = new DefaultKafkaProducerFactory<>(props);
        customizers.orderedStream().forEach(c -> c.customize(factory)); // e.g. Micrometer producer metrics
        return factory;
    }

    @Bean
    public KafkaTemplate<String, byte[]> kafkaTemplate(ProducerFactory<String, byte[]> producerFactory) {
        return new KafkaTemplate<>(producerFactory);
    }

    @Bean
    public ConsumerFactory<String, byte[]> consumerFactory(KafkaProperties kafkaProperties,
                                                          ObjectProvider<SslBundles> sslBundles,
                                                          ObjectProvider<DefaultKafkaConsumerFactoryCustomizer> customizers) {
        Map<String, Object> props = kafkaProperties.buildConsumerProperties(sslBundles.getIfAvailable());
        props.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        props.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, ByteArrayDeserializer.class);
        props.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, false);
        DefaultKafkaConsumerFactory<String, byte[]> factory = new DefaultKafkaConsumerFactory<>(props);
        customizers.orderedStream().forEach(c -> c.customize(factory)); // exposes kafka_consumer_* metrics (lag)
        return factory;
    }

    @Bean
    public DefaultErrorHandler kafkaErrorHandler(KafkaTemplate<String, byte[]> kafkaTemplate,
                                                 IngestionKafkaProperties appProps,
                                                 IngestionMetrics metrics) {
        DeadLetterPublishingRecoverer dlpr = new DeadLetterPublishingRecoverer(kafkaTemplate,
                // partition -1: let the producer pick (the DLT has 1 partition, the input has several)
                (record, ex) -> new TopicPartition(appProps.deadLetterTopic(), -1));
        dlpr.setHeadersFunction((record, ex) -> DeadLetters.headers(record, DeadLetters.reason(ex)));
        dlpr.setWaitForSendResultTimeout(appProps.sendTimeout());

        IngestionKafkaProperties.Retry retry = appProps.retry();
        ExponentialBackOffWithMaxRetries backOff = new ExponentialBackOffWithMaxRetries(retry.maxRetries());
        backOff.setInitialInterval(retry.initialInterval().toMillis());
        backOff.setMultiplier(retry.multiplier());
        backOff.setMaxInterval(retry.maxInterval().toMillis());

        DefaultErrorHandler handler = new DefaultErrorHandler((record, ex) -> {
            dlpr.accept(record, ex);
            metrics.error();
            log.error("event=ingestion_dead_lettered topic={} partition={} offset={} key={} reason=\"{}\"",
                    record.topic(), record.partition(), record.offset(), record.key(), DeadLetters.reason(ex));
        }, backOff);
        handler.setCommitRecovered(true);
        return handler;
    }

    @Bean
    public ConcurrentKafkaListenerContainerFactory<String, byte[]> kafkaListenerContainerFactory(
            ConsumerFactory<String, byte[]> consumerFactory,
            KafkaProperties kafkaProperties,
            DefaultErrorHandler kafkaErrorHandler) {
        ConcurrentKafkaListenerContainerFactory<String, byte[]> factory = new ConcurrentKafkaListenerContainerFactory<>();
        factory.setConsumerFactory(consumerFactory);
        factory.setBatchListener(true);
        factory.getContainerProperties().setAckMode(ContainerProperties.AckMode.MANUAL_IMMEDIATE);
        Integer concurrency = kafkaProperties.getListener().getConcurrency();
        if (concurrency != null) {
            factory.setConcurrency(concurrency);
        }
        factory.setCommonErrorHandler(kafkaErrorHandler);
        return factory;
    }
}
