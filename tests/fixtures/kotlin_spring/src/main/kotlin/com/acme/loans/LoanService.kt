package com.acme.loans

import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

const val MAX_AUTO_APPROVAL = 50_000.0

@Service
class LoanService(private val repository: LoanRepository, private val scoring: ScoringClient) {

    /** Business rule: loans above MAX_AUTO_APPROVAL need a credit score above 700. */
    @Transactional
    fun approve(id: Long): Loan {
        val loan = repository.findById(id).orElseThrow()
        require(loan.amount > 0) { "amount must be positive" }
        val score = scoring.scoreFor(loan.customerId)
        loan.status = if (loan.amount <= MAX_AUTO_APPROVAL || score > 700) LoanStatus.APPROVED else LoanStatus.REJECTED
        return repository.save(loan)
    }

    fun forCustomer(customerId: Long): List<Loan> = repository.findByCustomerId(customerId)
}

class ScoringClient {
    fun scoreFor(customerId: Long): Int = 650
}
