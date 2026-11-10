// Versions have NOT been verified by an actual build (the sandbox cannot reach Google/Maven). AGP 8.5.2 needs Gradle 8.7+; the wrapper pins 8.9.
// If the build complains, bump the plugin versions to the ones Android Studio / the error message suggests.
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
