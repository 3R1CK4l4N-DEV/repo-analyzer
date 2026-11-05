export abstract class BaseRepository<T> {
  protected items: T[] = [];

  findAll(): Promise<T[]> {
    return Promise.resolve(this.items);
  }

  findOne(criteria: unknown): Promise<T> {
    return Promise.resolve(this.items[0]);
  }

  save(entity: T): Promise<T> {
    this.items.push(entity);
    return Promise.resolve(entity);
  }
}
