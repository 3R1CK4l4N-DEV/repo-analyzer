package com.acme.bank.repository;

import org.springframework.data.jpa.repository.JpaRepository;

import com.acme.bank.model.Account;

public interface AccountRepository extends JpaRepository<Account, Long> {
}
