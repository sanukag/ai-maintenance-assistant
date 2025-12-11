# Manual lifecycle management

Manual lifecycle management prevents outdated maintenance information from
silently contributing to new answers while retaining an auditable local history.

## States

Every stored manual revision has one of three states:

- `current` revisions may contribute chunks to search and grounded answers;
- `superseded` revisions were replaced by a newer revision and remain available
  only for deliberate inspection; and
- `archived` revisions are retained locally but excluded from retrieval.

New uploads begin at revision 1 as `current`. Installing a replacement creates a
new document identifier and increments the revision number. The new record and
its chunks, vectors and managed source file are stored before the previous
current record becomes `superseded` in the same database transaction.

## Retrieval boundary

Lifecycle filtering is enforced in SQLite vector retrieval, not only in the web
interface. Search joins chunks to their document and accepts `current` records
only. Supplying a superseded or archived document identifier therefore returns
no evidence.

The Assistant manual selector also requests current records explicitly. The
Manuals page starts in the current view; workers must choose **All revisions**
to inspect retained copies.

## Worker operations

The **Manage** control for a current manual provides:

- **Install a newer revision** to retain the old record as superseded and make
  the new copy current;
- **Re-index manual** to regenerate every vector with the configured embedding
  model;
- **Archive** to retain the record while excluding it from future answers; and
- **Delete permanently** to remove the source file, metadata, chunks and vectors.

Archive and permanent deletion require a separate confirmation step. Deletion
first moves the managed source directory aside, deletes the cascading SQLite
records in a transaction, restores the directory if the transaction fails and
removes the staged directory only after commit.

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/documents?lifecycle_status=current` | Filter the document list by state |
| `GET` | `/documents/{id}/revisions` | Return retained revisions oldest first |
| `POST` | `/documents/{id}/revisions` | Upload and install a replacement revision |
| `POST` | `/documents/{id}/reindex` | Rebuild chunks, parent context and vectors |
| `POST` | `/documents/{id}/archive` | Exclude a retained manual from retrieval |
| `DELETE` | `/documents/{id}` | Permanently remove the complete stored record |

An identical replacement returns HTTP `409` with `identical_revision`. Replacing
a non-current record returns `revision_conflict`. Missing records use
`document_not_found`, and re-indexing without an embedding provider returns
`embeddings_disabled`.
