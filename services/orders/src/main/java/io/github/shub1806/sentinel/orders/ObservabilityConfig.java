package io.github.shub1806.sentinel.orders;

import io.micrometer.observation.ObservationPredicate;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.server.observation.ServerRequestObservationContext;

@Configuration(proxyBeanMethods = false)
class ObservabilityConfig {

    /**
     * Don't observe Kubernetes probes, Prometheus scrapes or the outbox poll (every 500 ms).
     * Together they would create about 150 traces a minute and bury the real ones.
     * This also drops their HTTP metrics, which the SLOs already leave out.
     */
    @Bean
    ObservationPredicate skipBackgroundNoise() {
        return (name, context) -> {
            if ("tasks.scheduled.execution".equals(name)) {
                return false;
            }
            if (context instanceof ServerRequestObservationContext request) {
                return !request.getCarrier().getRequestURI().startsWith("/actuator");
            }
            return true;
        };
    }
}
