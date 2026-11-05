package org.acme.inventory;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.transaction.Transactional;

@ApplicationScoped
public class InventoryService {
    @Inject
    ItemRepository repository;

    /** Business rule: stock never goes below zero. */
    @Transactional
    public Item withdraw(String sku, int qty) {
        Item item = repository.findBySku(sku);
        if (item.quantity - qty < 0) {
            throw new IllegalStateException("insufficient stock");
        }
        item.quantity -= qty;
        return item;
    }
}
