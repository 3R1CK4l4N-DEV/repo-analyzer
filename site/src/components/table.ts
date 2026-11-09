export interface TableColumn<T> {
  label: string;
  value: (row: T) => string | HTMLElement;
  search?: (row: T) => string;
}

export interface DataTableOptions<T> {
  placeholder?: string;
  filter?: (row: T, query: string) => boolean;
  controls?: HTMLElement[];
  emptyMessage?: string;
}

export function createDataTable<T>(rows: T[], columns: TableColumn<T>[], options: DataTableOptions<T> = {}): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.className = "data-table";
  const toolbar = document.createElement("div");
  toolbar.className = "table-toolbar";
  const input = document.createElement("input");
  input.type = "search";
  input.placeholder = options.placeholder ?? "Filtrar…";
  input.setAttribute("aria-label", input.placeholder);
  toolbar.append(input, ...(options.controls ?? []));
  wrapper.append(toolbar);

  const viewport = document.createElement("div");
  viewport.className = "table-scroll";
  const table = document.createElement("table");
  const head = table.createTHead().insertRow();
  for (const column of columns) {
    const cell = document.createElement("th");
    cell.textContent = column.label;
    head.append(cell);
  }
  const body = table.createTBody();
  table.append(body);
  viewport.append(table);
  wrapper.append(viewport);

  const render = () => {
    const query = input.value.trim().toLocaleLowerCase("es");
    body.replaceChildren();
    const visible = rows.filter((row) => {
      if (options.filter) return options.filter(row, query);
      if (!query) return true;
      return columns.some((column) => (column.search?.(row) ?? String(column.value(row))).toLocaleLowerCase("es").includes(query));
    });
    if (!visible.length) {
      const row = body.insertRow();
      const cell = row.insertCell();
      cell.colSpan = columns.length;
      cell.className = "empty-cell";
      cell.textContent = options.emptyMessage ?? "Sin resultados";
      return;
    }
    for (const item of visible) {
      const row = body.insertRow();
      for (const column of columns) {
        const cell = row.insertCell();
        const value = column.value(item);
        if (value instanceof HTMLElement) cell.append(value);
        else cell.textContent = value;
      }
    }
  };
  input.addEventListener("input", render);
  for (const control of options.controls ?? []) control.addEventListener("change", render);
  render();
  return wrapper;
}
