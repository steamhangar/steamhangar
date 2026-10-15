package dev.steamvault.app.storage

import dev.steamvault.app.ui.library.logic.LibraryLayout
import dev.steamvault.app.ui.library.logic.LibrarySort
import dev.steamvault.app.ui.library.logic.MetaFilter

/**
 * In-memory [LibraryPreferences] fake for JVM tests (WP 4b.4), same shape
 * as [InMemoryCredentialStore]: a plain field, no `SharedPreferences`
 * involved, so anything depending on [LibraryPreferences] is testable on
 * the JVM without a device.
 */
class InMemoryLibraryPreferences(initial: LibraryLayout = LibraryLayout.DEFAULT) : LibraryPreferences {
    private var layout: LibraryLayout = initial

    override fun getLayout(): LibraryLayout = layout
    override fun setLayout(layout: LibraryLayout) {
        this.layout = layout
    }

    private var sort: LibrarySort = LibrarySort.DEFAULT

    override fun getSort(): LibrarySort = sort
    override fun setSort(sort: LibrarySort) {
        this.sort = sort
    }

    private var metaFilter: MetaFilter = MetaFilter.DEFAULT

    override fun getMetaFilter(): MetaFilter = metaFilter
    override fun setMetaFilter(filter: MetaFilter) {
        this.metaFilter = filter
    }
}
