import type { TableRead } from "../api/types";

type TableNameParts = Pick<TableRead, "schema_name" | "table_name" | "qualified_table_name" | "catalog_name">;

export const schemaTableName = (table: TableNameParts): string =>
  `${table.schema_name}.${table.table_name}`;

export const qualifiedTableName = (table: TableNameParts): string =>
  table.qualified_table_name ?? schemaTableName(table);

export const tableTooltipName = (table: TableNameParts): string => qualifiedTableName(table);
