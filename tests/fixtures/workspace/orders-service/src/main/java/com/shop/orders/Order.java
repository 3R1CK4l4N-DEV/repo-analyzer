package com.shop.orders;

import jakarta.persistence.Entity;

@Entity
public class Order { public Long id; public double total; public Long customerId; }
