import { Column, Entity, PrimaryGeneratedColumn } from 'typeorm';

@Entity()
export class Order {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column()
  total: number;

  @Column({ default: 'CONFIRMED' })
  status: string;
}

export enum OrderStatus {
  CONFIRMED = 'CONFIRMED',
  PENDING_APPROVAL = 'PENDING_APPROVAL',
}
