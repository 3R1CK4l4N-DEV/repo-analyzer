import { MigrationInterface, QueryRunner } from "typeorm";

export class Init1700000000000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`CREATE TABLE "order" ("id" SERIAL NOT NULL, "total" numeric NOT NULL, "status" varchar NOT NULL, CONSTRAINT "PK_order" PRIMARY KEY ("id"))`);
    await queryRunner.query(`CREATE TABLE "order_item" ("id" SERIAL NOT NULL, "orderId" integer, "qty" integer NOT NULL, CONSTRAINT "PK_item" PRIMARY KEY ("id"))`);
    await queryRunner.query(`ALTER TABLE "order_item" ADD CONSTRAINT "FK_item_order" FOREIGN KEY ("orderId") REFERENCES "order"("id")`);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE "order_item"`);
    await queryRunner.query(`DROP TABLE "order"`);
  }
}
