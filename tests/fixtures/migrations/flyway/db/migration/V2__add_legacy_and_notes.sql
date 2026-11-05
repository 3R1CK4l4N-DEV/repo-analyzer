CREATE TABLE legacy_tmp (id INT);
ALTER TABLE orders ADD COLUMN notes TEXT;
ALTER TABLE orders ADD CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (id);
DROP TABLE legacy_tmp;
ALTER TABLE customers DROP COLUMN name;
