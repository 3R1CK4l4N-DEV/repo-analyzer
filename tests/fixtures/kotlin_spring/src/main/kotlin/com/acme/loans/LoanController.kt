package com.acme.loans

import org.springframework.web.bind.annotation.*

@RestController
@RequestMapping("/loans")
class LoanController(private val service: LoanService) {

    @GetMapping("/customer/{customerId}")
    fun byCustomer(@PathVariable customerId: Long): List<Loan> = service.forCustomer(customerId)

    @PostMapping("/{id}/approve")
    fun approve(@PathVariable id: Long): Loan = service.approve(id)
}
