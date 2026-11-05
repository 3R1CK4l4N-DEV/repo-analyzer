package com.acme.catalog;

import io.micronaut.serde.annotation.Serdeable;

@Serdeable
public record Product(Long id, String name, double price) {}
