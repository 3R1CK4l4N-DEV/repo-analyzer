package org.acme.inventory;

import jakarta.inject.Inject;
import jakarta.ws.rs.*;
import jakarta.ws.rs.core.MediaType;

@Path("/items")
@Produces(MediaType.APPLICATION_JSON)
public class ItemResource {
    @Inject
    InventoryService service;

    @GET
    @Path("/{sku}")
    public Item get(@PathParam("sku") String sku) { return service.repository.findBySku(sku); }

    @POST
    @Path("/{sku}/withdraw")
    public Item withdraw(@PathParam("sku") String sku, @QueryParam("qty") int qty) { return service.withdraw(sku, qty); }

    @GET
    public java.util.List<Item> list() { return Item.listAll(); }
}
