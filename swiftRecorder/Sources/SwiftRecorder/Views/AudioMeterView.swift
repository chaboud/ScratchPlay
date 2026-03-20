import SwiftUI

/// A VU meter bar showing audio levels with peak hold.
public struct AudioMeterView: View {
    let peakLevel: Float     // dBFS
    let averageLevel: Float  // dBFS
    let peakHold: Float      // dBFS

    private let minDB: Float = -60
    private let maxDB: Float = 0

    public init(peakLevel: Float, averageLevel: Float, peakHold: Float) {
        self.peakLevel = peakLevel
        self.averageLevel = averageLevel
        self.peakHold = peakHold
    }

    private func fraction(_ db: Float) -> CGFloat {
        let clamped = max(minDB, min(maxDB, db))
        return CGFloat((clamped - minDB) / (maxDB - minDB))
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("AUDIO")
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .foregroundColor(.secondary)

            GeometryReader { geo in
                let w = geo.size.width
                let h = geo.size.height

                ZStack(alignment: .leading) {
                    // Background
                    Rectangle()
                        .fill(Color.black.opacity(0.3))

                    // RMS bar with color zones
                    HStack(spacing: 0) {
                        let rmsFrac = fraction(averageLevel)
                        let greenEnd: CGFloat = fraction(-12)
                        let yellowEnd: CGFloat = fraction(-3)

                        // Green zone
                        Rectangle()
                            .fill(Color.green)
                            .frame(width: min(rmsFrac, greenEnd) * w)

                        // Yellow zone
                        if rmsFrac > greenEnd {
                            Rectangle()
                                .fill(Color.yellow)
                                .frame(width: min(rmsFrac - greenEnd, yellowEnd - greenEnd) * w)
                        }

                        // Red zone
                        if rmsFrac > yellowEnd {
                            Rectangle()
                                .fill(Color.red)
                                .frame(width: (rmsFrac - yellowEnd) * w)
                        }

                        Spacer(minLength: 0)
                    }

                    // Peak indicator (white line)
                    Rectangle()
                        .fill(Color.white)
                        .frame(width: 2)
                        .offset(x: fraction(peakLevel) * w)

                    // Peak hold indicator (yellow line)
                    Rectangle()
                        .fill(Color.yellow.opacity(0.8))
                        .frame(width: 1)
                        .offset(x: fraction(peakHold) * w)
                }
                .clipShape(RoundedRectangle(cornerRadius: 2))
            }
            .frame(height: 12)

            // Scale markings
            HStack {
                ForEach([-48, -36, -24, -12, -6, 0], id: \.self) { db in
                    Text("\(db)")
                        .font(.system(size: 7, design: .monospaced))
                        .foregroundColor(.secondary)
                    if db != 0 { Spacer() }
                }
            }
        }
        .frame(height: 30)
    }
}
