package com.acme.loans

import org.springframework.data.jpa.repository.JpaRepository

interface LoanRepository : JpaRepository<Loan, Long> {
    fun findByCustomerId(customerId: Long): List<Loan>
}
