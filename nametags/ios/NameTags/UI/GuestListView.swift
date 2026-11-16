import SwiftUI

struct GuestListView: View {
    @EnvironmentObject private var model: AppModel
    @State private var query = ""
    @State private var pickingEvent = false
    @State private var scanning = false
    @FocusState private var searchFocused: Bool

    var body: some View {
        let results = model.results(for: query)
        VStack(spacing: 0) {
            header(count: results.count)
            ScrollView {
                LazyVStack(spacing: 0) {
                    content(results)
                }
                .readableWidth()
            }
            .scrollDismissesKeyboard(.immediately)
            .refreshable {
                await model.loadEvents()
                await model.loadGuests()
            }
        }
        .background(Theme.ground.ignoresSafeArea())
        .navigationTitle("名牌")
        .toolbar(.hidden, for: .navigationBar)
        .onChange(of: model.listEvent) { _, _ in query = "" }
        .sheet(isPresented: $pickingEvent) {
            EventPickerView().environmentObject(model).tint(Theme.ink)
        }
        .fullScreenCover(isPresented: $scanning) {
            ScanView().environmentObject(model).tint(Theme.ink).transaction { $0.animation = nil }
        }
    }

    private func header(count: Int) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center) {
                Text("名牌")
                    .font(.system(size: 34, weight: .heavy))
                    .foregroundStyle(Theme.ink)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                PrinterBadge(status: model.printer)
                Menu {
                    Button("重新載入名單") {
                        Task {
                            await model.loadEvents()
                            await model.loadGuests()
                        }
                    }
                    Button("登出", role: .destructive) { model.signOut() }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(Theme.ink)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("更多")
                .accessibilityIdentifier("moreMenu")
            }
            if !model.events.isEmpty { eventButton }
            HStack(spacing: 8) {
                searchField
                Button { scanning = true } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "qrcode.viewfinder").font(.system(size: 17, weight: .semibold))
                        Text("掃描")
                    }
                }
                .buttonStyle(BlockButtonStyle(wide: false))
                .accessibilityLabel("掃描")
                .accessibilityIdentifier("scanButton")
            }
            Text(countLine(count))
                .font(.system(size: 14))
                .foregroundStyle(Theme.muted)
                .accessibilityIdentifier("countLine")
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .padding(.bottom, 10)
        .readableWidth()
        .background(Theme.ground)
    }

    /// The event the list is for; tap → pick another.
    private var eventButton: some View {
        Button { pickingEvent = true } label: {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 1) {
                    Text("活動").font(.system(size: 13)).foregroundStyle(Theme.muted)
                    Text(model.currentEvent?.name ?? "預設活動")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(Theme.ink)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.down")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Theme.ink)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .frame(minHeight: 48)
            .background(Theme.panel)
            .overlay(Rectangle().stroke(Theme.rule, lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("eventPicker")
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Theme.muted)
            TextField("搜尋姓名、機構、職位", text: $query)
                .font(.system(size: 17))
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .focused($searchFocused)
                .accessibilityIdentifier("searchField")
            if !query.isEmpty {
                Button {
                    query = ""
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(Theme.ink)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("清除")
                .accessibilityIdentifier("clearSearch")
            }
        }
        .padding(.leading, 12)
        .frame(minHeight: 48)
        .background(Theme.panel)
        .overlay(Rectangle().stroke(searchFocused ? Theme.ink : Theme.rule, lineWidth: searchFocused ? 2 : 1))
    }

    private func countLine(_ n: Int) -> String {
        if model.guests.isEmpty { return model.loadingGuests ? "載入名單中…" : "名單是空的" }
        if query.trimmingCharacters(in: .whitespaces).isEmpty { return "共 \(model.guests.count) 位嘉賓" }
        return "找到 \(n) 位（共 \(model.guests.count) 位）"
    }

    @ViewBuilder
    private func content(_ results: [Guest]) -> some View {
        if let err = model.guestsError, model.guests.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                Text("載入名單失敗").font(.system(size: 17, weight: .semibold))
                Text(err).font(.system(size: 15)).foregroundStyle(Theme.muted)
                Button("再試") { Task { await model.loadGuests() } }
                    .buttonStyle(BlockButtonStyle(primary: false))
            }
            .foregroundStyle(Theme.ink)
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.panel)
        } else if results.isEmpty && !model.guests.isEmpty {
            Text("沒有符合「\(query)」的嘉賓")
                .font(.system(size: 15))
                .foregroundStyle(Theme.muted)
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.panel)
        } else {
            ForEach(results) { g in
                NavigationLink(value: g) { GuestRow(guest: g) }
                    .buttonStyle(RowButtonStyle())
                Rectangle().fill(Theme.rule).frame(height: 1).padding(.leading, 16)
            }
        }
    }
}

struct GuestRow: View {
    let guest: Guest

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(guest.displayName)
                        .font(.system(size: 17, weight: .semibold))
                    if guest.isWithdrawn { WithdrawnLabel() }
                }
                if let i = guest.institution {
                    Text(i).font(.system(size: 15))
                }
                if let t = guest.title {
                    Text(t).font(.system(size: 13)).foregroundStyle(Theme.muted)
                }
            }
            .multilineTextAlignment(.leading)
            .foregroundStyle(guest.isWithdrawn ? Theme.muted : Theme.ink)
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Theme.muted)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(minHeight: 56)
        .accessibilityIdentifier("guest-\(guest.id)")
    }
}

struct WithdrawnLabel: View {
    var body: some View {
        Text("Withdrawn")
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(Theme.muted)
            .padding(.horizontal, 6)
            .padding(.vertical, 1)
            .overlay(Rectangle().stroke(Theme.muted, lineWidth: 1))
    }
}
