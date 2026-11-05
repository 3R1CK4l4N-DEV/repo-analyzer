package com.acme.catalog;

import jakarta.inject.Singleton;

@Singleton
public class PricingService {
    private final ProductRepository repository;

    public PricingService(ProductRepository repository) { this.repository = repository; }

    /** Business rule: prices below 1.00 are not allowed. */
    public Product reprice(Long id, double price) {
        if (price < 1.0) throw new IllegalArgumentException("price too low");
        Product p = repository.findById(id).orElseThrow();
        return repository.update(new Product(p.id(), p.name(), price));
    }
}
