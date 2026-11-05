package com.acme.bank.model;

public abstract class BaseEntity {
    protected long version;
}

interface Auditable {
    default String auditTag() { return "audit"; }
}
