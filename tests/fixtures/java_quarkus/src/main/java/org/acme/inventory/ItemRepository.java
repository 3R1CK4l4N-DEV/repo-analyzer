package org.acme.inventory;

import io.quarkus.hibernate.orm.panache.PanacheRepository;
import jakarta.enterprise.context.ApplicationScoped;

@ApplicationScoped
public class ItemRepository implements PanacheRepository<Item> {
    public Item findBySku(String sku) { return find("sku", sku).firstResult(); }
}
