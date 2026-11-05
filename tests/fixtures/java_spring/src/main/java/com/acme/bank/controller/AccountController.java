package com.acme.bank.controller;

import java.util.List;

import org.springframework.web.bind.annotation.*;

import com.acme.bank.model.Account;
import com.acme.bank.service.AccountService;

/**
 * REST API for bank accounts.
 */
@RestController
@RequestMapping("/accounts")
public class AccountController {

    private final AccountService accountService;

    public AccountController(AccountService accountService) {
        this.accountService = accountService;
    }

    @GetMapping
    public List<Account> list() {
        return accountService.findAll();
    }

    @GetMapping("/{id}")
    public Account get(@PathVariable Long id) {
        return accountService.findById(id);
    }

    @PostMapping(value = "/{id}/deposit")
    public Account deposit(@PathVariable Long id, @RequestParam double amount) {
        return accountService.deposit(id, amount);
    }

    @RequestMapping(path = "/{id}", method = RequestMethod.DELETE)
    public void close(@PathVariable Long id) {
        accountService.close(id);
    }
}
