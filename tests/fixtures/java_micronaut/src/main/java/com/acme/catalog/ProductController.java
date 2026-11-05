package com.acme.catalog;

import io.micronaut.http.annotation.*;

@Controller("/products")
public class ProductController {
    private final PricingService pricing;

    public ProductController(PricingService pricing) { this.pricing = pricing; }

    @Get("/{id}")
    public Product get(@PathVariable Long id) { return pricing.reprice(id, 1.0); }

    @Put(uri = "/{id}/price")
    public Product reprice(@PathVariable Long id, @QueryValue double price) { return pricing.reprice(id, price); }
}
