package com.acme.loans

import jakarta.persistence.Entity
import jakarta.persistence.Id

enum class LoanStatus { REQUESTED, APPROVED, REJECTED }

/** A loan requested by a customer. */
@Entity
data class Loan(
    @Id val id: Long,
    val customerId: Long,
    var amount: Double,
    var status: LoanStatus = LoanStatus.REQUESTED,
)
