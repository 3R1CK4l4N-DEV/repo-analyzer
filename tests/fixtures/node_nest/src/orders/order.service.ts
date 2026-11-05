import { Injectable } from '@nestjs/common';
import { OrderRepository } from './order.repository';
import { Order } from './order.entity';
import { CreateOrderDto } from './order.controller';

const MAX_ORDER_TOTAL = 5000;

@Injectable()
export class OrderService {
  constructor(private readonly repository: OrderRepository) {}

  findAll(): Promise<Order[]> {
    return this.repository.findAll();
  }

  findOne(id: string): Promise<Order> {
    return this.repository.findById(id);
  }

  /**
   * Business rule: an order above MAX_ORDER_TOTAL requires manual approval.
   */
  async create(dto: CreateOrderDto): Promise<Order> {
    if (dto.total <= 0) {
      throw new Error('Order total must be positive');
    }
    const order = new Order();
    order.total = dto.total;
    order.status = dto.total > MAX_ORDER_TOTAL ? 'PENDING_APPROVAL' : 'CONFIRMED';
    return this.repository.save(order);
  }
}
