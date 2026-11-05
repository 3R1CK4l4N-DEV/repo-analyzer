package com.shop.orders;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/orders")
public class OrderController {
    private final OrderService service;
    public OrderController(OrderService service) { this.service = service; }

    @PostMapping
    public Order create(@RequestBody Order order) { return service.place(order); }

    @GetMapping("/{id}")
    public Order get(@PathVariable Long id) { return service.find(id); }
}
