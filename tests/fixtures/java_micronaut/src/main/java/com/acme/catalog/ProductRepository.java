package com.acme.catalog;

import io.micronaut.data.jdbc.annotation.JdbcRepository;
import io.micronaut.data.repository.CrudRepository;

@JdbcRepository
public interface ProductRepository extends CrudRepository<Product, Long> {}
