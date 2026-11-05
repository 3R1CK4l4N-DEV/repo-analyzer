import { Injectable } from '@nestjs/common';
import { BaseRepository } from '../common/base.repository';
import { Order } from './order.entity';

@Injectable()
export class OrderRepository extends BaseRepository<Order> {
  findById(id: string): Promise<Order> {
    return this.findOne({ where: { id } });
  }
}
