/**
 * Small helpers shared by the Connections page and its sub-components.
 */

export const formatSourceTestSuccessMessage = (namespaceCount: number): string => {
  const noun = namespaceCount === 1 ? "schema" : "schemas";
  return `Connected — found ${namespaceCount} ${noun} in catalog`;
};