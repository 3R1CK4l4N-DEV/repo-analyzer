package com.acme.bank.service;

import java.util.List;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.acme.bank.model.Account;
import com.acme.bank.repository.AccountRepository;

@Service
public class AccountService {

    private static final double MAX_DEPOSIT = 10_000.0;

    private final AccountRepository repository;

    public AccountService(AccountRepository repository) {
        this.repository = repository;
    }

    public List<Account> findAll() {
        return repository.findAll();
    }

    public Account findById(Long id) {
        return repository.findById(id).orElseThrow();
    }

    /**
     * Deposits money. Business rule: a single deposit cannot exceed MAX_DEPOSIT.
     */
    @Transactional
    public Account deposit(Long id, double amount) {
        if (amount <= 0 || amount > MAX_DEPOSIT) {
            throw new IllegalArgumentException("Invalid deposit amount");
        }
        Account account = findById(id);
        account.setBalance(account.getBalance() + amount);
        return repository.save(account);
    }

    @Transactional
    public void close(Long id) {
        Account account = findById(id);
        if (account.getBalance() != 0) {
            throw new IllegalStateException("Balance must be zero to close");
        }
        repository.delete(account);
    }
}
