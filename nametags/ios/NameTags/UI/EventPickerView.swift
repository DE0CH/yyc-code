import SwiftUI

/// The events, newest first; the one the list is for is marked ■. Picking one is remembered.
struct EventPickerView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(model.events) { e in
                        Button {
                            model.choose(e)
                            dismiss()
                        } label: {
                            EventRow(event: e, selected: e.id == model.currentEvent?.id)
                        }
                        .buttonStyle(RowButtonStyle())
                        .accessibilityIdentifier("event-\(e.id)")
                        Rectangle().fill(Theme.rule).frame(height: 1).padding(.leading, 16)
                    }
                }
                .readableWidth()
            }
            .background(Theme.ground.ignoresSafeArea())
            .navigationTitle("選擇活動")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }.accessibilityIdentifier("cancelEventPicker")
                }
            }
        }
    }
}

struct EventRow: View {
    let event: Event
    let selected: Bool

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Pip(on: selected)
            VStack(alignment: .leading, spacing: 3) {
                Text(event.name).font(.system(size: 17, weight: .semibold))
                if let en = event.nameEn, en != event.name {
                    Text(en).font(.system(size: 15))
                }
                if let d = event.detail {
                    Text(d).font(.system(size: 13)).foregroundStyle(Theme.muted)
                }
            }
            .multilineTextAlignment(.leading)
            .foregroundStyle(Theme.ink)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(minHeight: 56)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
