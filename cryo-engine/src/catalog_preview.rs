//! Catalog wrapper that previews expire_snapshots commits without persisting.

use async_trait::async_trait;
use iceberg::table::Table;
use iceberg::{
    Catalog, Namespace, NamespaceIdent, Result, TableCommit, TableCreation, TableIdent,
    TableUpdate,
};
use std::collections::HashMap;
use std::fmt::{self, Debug, Formatter};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

/// Wraps a catalog and records how many snapshots an expire commit would remove.
pub struct PreviewCatalog {
    inner: Arc<dyn Catalog>,
    expired_count: AtomicU64,
}

impl PreviewCatalog {
    pub fn new(inner: Arc<dyn Catalog>) -> Self {
        Self {
            inner,
            expired_count: AtomicU64::new(0),
        }
    }

    pub fn expired_count(&self) -> u64 {
        self.expired_count.load(Ordering::SeqCst)
    }
}

impl Debug for PreviewCatalog {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        f.debug_struct("PreviewCatalog").finish_non_exhaustive()
    }
}

fn count_remove_snapshots(updates: &[TableUpdate]) -> u64 {
    updates
        .iter()
        .filter_map(|update| match update {
            TableUpdate::RemoveSnapshots { snapshot_ids } => Some(snapshot_ids.len() as u64),
            _ => None,
        })
        .sum()
}

#[async_trait]
impl Catalog for PreviewCatalog {
    async fn list_namespaces(
        &self,
        parent: Option<&NamespaceIdent>,
    ) -> Result<Vec<NamespaceIdent>> {
        self.inner.list_namespaces(parent).await
    }

    async fn create_namespace(
        &self,
        namespace: &NamespaceIdent,
        properties: HashMap<String, String>,
    ) -> Result<Namespace> {
        self.inner.create_namespace(namespace, properties).await
    }

    async fn get_namespace(&self, namespace: &NamespaceIdent) -> Result<Namespace> {
        self.inner.get_namespace(namespace).await
    }

    async fn namespace_exists(&self, namespace: &NamespaceIdent) -> Result<bool> {
        self.inner.namespace_exists(namespace).await
    }

    async fn update_namespace(
        &self,
        namespace: &NamespaceIdent,
        properties: HashMap<String, String>,
    ) -> Result<()> {
        self.inner.update_namespace(namespace, properties).await
    }

    async fn drop_namespace(&self, namespace: &NamespaceIdent) -> Result<()> {
        self.inner.drop_namespace(namespace).await
    }

    async fn list_tables(&self, namespace: &NamespaceIdent) -> Result<Vec<TableIdent>> {
        self.inner.list_tables(namespace).await
    }

    async fn create_table(
        &self,
        namespace: &NamespaceIdent,
        creation: TableCreation,
    ) -> Result<Table> {
        self.inner.create_table(namespace, creation).await
    }

    async fn load_table(&self, table: &TableIdent) -> Result<Table> {
        self.inner.load_table(table).await
    }

    async fn drop_table(&self, table: &TableIdent) -> Result<()> {
        self.inner.drop_table(table).await
    }

    async fn purge_table(&self, table: &TableIdent) -> Result<()> {
        self.inner.purge_table(table).await
    }

    async fn table_exists(&self, table: &TableIdent) -> Result<bool> {
        self.inner.table_exists(table).await
    }

    async fn rename_table(&self, src: &TableIdent, dest: &TableIdent) -> Result<()> {
        self.inner.rename_table(src, dest).await
    }

    async fn register_table(
        &self,
        table: &TableIdent,
        metadata_location: String,
    ) -> Result<Table> {
        self.inner.register_table(table, metadata_location).await
    }

    async fn update_table(&self, commit: TableCommit) -> Result<Table> {
        let mut commit = commit;
        let requirements = commit.take_requirements();
        let updates = commit.take_updates();
        self.expired_count
            .store(count_remove_snapshots(&updates), Ordering::SeqCst);

        let table = self.inner.load_table(commit.identifier()).await?;
        for requirement in &requirements {
            requirement.check(Some(table.metadata()))?;
        }
        Ok(table)
    }
}
