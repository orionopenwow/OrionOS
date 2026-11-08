// Orion Mobile: no third-party libraries — only Android's own APIs (keystore, BiometricPrompt,
// JobScheduler, HttpURLConnection, org.json, MediaRecorder, TextToSpeech), so it builds anywhere Android Studio is installed.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.orion.mobile"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.orion.mobile"
        minSdk = 30          // Android 11+: per-use biometric keys (setUserAuthenticationParameters)
        targetSdk = 34
        versionCode = 3      // bump both on every APK you hand out: Android refuses to install an older code
        versionName = "0.3.0" // 0.3: Missions (send work to the PC), Pocket Orion fallback
    }

    buildTypes {
        release {
            // R8 stays off: the app is small, has no libraries to shrink, and looks up a few Android 12+ APIs
            // by reflection (Glass.frost), which shrinking could break. Nothing to keep in a ProGuard file.
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}
