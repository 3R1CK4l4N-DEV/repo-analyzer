package com.shop.orders;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

@Service
public class OrderService {
    private final KafkaTemplate<String, Order> kafka;
    private final RestTemplate http = new RestTemplate();

    public OrderService(KafkaTemplate<String, Order> kafka) { this.kafka = kafka; }

    public Order place(Order order) {
        if (order.total <= 0) throw new IllegalArgumentException("total must be positive");
        Invoice inv = http.postForObject("http://billing-service/invoices", order, Invoice.class);
        kafka.send("orders.placed", order);
        return order;
    }

    public Order find(Long id) { return new Order(); }
}
