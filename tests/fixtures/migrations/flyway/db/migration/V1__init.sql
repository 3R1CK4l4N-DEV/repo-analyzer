-- customers and orders
CREATE TABLE customers (
  id BIGINT PRIMARY KEY,
  email VARCHAR(255) NOT NULL,
  name VARCHAR(100)
);
CREATE TABLE orders (
  id BIGINT NOT NULL,
  customer_id BIGINT NOT NULL REFERENCES customers(id),
  total DECIMAL(12,2) NOT NULL,
  status VARCHAR(20) DEFAULT 'NEW',
  CONSTRAINT pk_orders PRIMARY KEY (id)
);
