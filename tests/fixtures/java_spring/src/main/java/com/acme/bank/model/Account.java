package com.acme.bank.model;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;

@Entity
public class Account extends BaseEntity implements Auditable {

    @Id
    private Long id;

    private String owner;

    private double balance;

    public Long getId() { return id; }

    public double getBalance() { return balance; }

    public void setBalance(double balance) { this.balance = balance; }

    public enum Status { OPEN, CLOSED }
}
