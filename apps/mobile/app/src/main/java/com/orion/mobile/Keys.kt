package com.orion.mobile

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.security.keystore.StrongBoxUnavailableException
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * The phone's two keys, both generated inside Android's hardware keystore (StrongBox when the phone has one).
 * They never leave the chip; the app only ever sees public keys and signatures.
 *
 *  READ  signs requests that only read (queue, notifications). Usable without a fingerprint.
 *  SIGN  signs approvals and rejections. Each use needs a fresh fingerprint (BiometricPrompt + CryptoObject),
 *        and the key is destroyed if a new fingerprint is enrolled on the phone.
 */
object Keys {
    const val READ = "orion_read_v1"
    const val SIGN = "orion_sign_v1"
    private const val STORE = "AndroidKeyStore"

    private fun ks(): KeyStore = KeyStore.getInstance(STORE).apply { load(null) }

    fun exists(): Boolean = ks().containsAlias(READ) && ks().containsAlias(SIGN)

    /** Creates both keys if missing. Throws IllegalStateException with a readable reason (e.g. no fingerprint set up). */
    fun ensure() {
        if (!ks().containsAlias(READ)) generate(READ, needsFingerprint = false)
        if (!ks().containsAlias(SIGN)) generate(SIGN, needsFingerprint = true)
    }

    fun deleteAll() {
        val k = ks()
        if (k.containsAlias(READ)) k.deleteEntry(READ)
        if (k.containsAlias(SIGN)) k.deleteEntry(SIGN)
    }

    private fun generate(alias: String, needsFingerprint: Boolean) {
        fun spec(strongBox: Boolean): KeyGenParameterSpec {
            val b = KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setIsStrongBoxBacked(strongBox)
            if (needsFingerprint) {
                b.setUserAuthenticationRequired(true)
                    .setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG) // 0 = every single use
                    .setInvalidatedByBiometricEnrollment(true)
            }
            return b.build()
        }
        val gen = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, STORE)
        try {
            gen.initialize(spec(strongBox = true))
            gen.generateKeyPair()
        } catch (e: StrongBoxUnavailableException) {
            gen.initialize(spec(strongBox = false))  // still hardware-backed (TEE), just not a separate chip
            gen.generateKeyPair()
        } catch (e: IllegalStateException) {
            throw IllegalStateException("Set up a fingerprint (or face unlock) on this phone first, then try again.")
        } catch (e: java.security.InvalidAlgorithmParameterException) {
            if (needsFingerprint) {
                throw IllegalStateException("Set up a fingerprint (or face unlock) on this phone first, then try again.")
            }
            throw e
        }
    }

    /** X.509 SubjectPublicKeyInfo DER, which the lab loads with cryptography's load_der_public_key. */
    fun publicDer(alias: String): ByteArray = ks().getCertificate(alias).publicKey.encoded

    private fun privateKey(alias: String): PrivateKey = ks().getKey(alias, null) as PrivateKey

    fun signWithRead(message: ByteArray): ByteArray =
        Signature.getInstance("SHA256withECDSA").run {
            initSign(privateKey(READ))
            update(message)
            sign()
        }

    /** A Signature ready to be unlocked by BiometricPrompt; call update()/sign() only after authentication. */
    fun signatureForApproval(): Signature =
        Signature.getInstance("SHA256withECDSA").apply { initSign(privateKey(SIGN)) }

    /** Same format as the lab's phones.fingerprint(): first 20 hex chars of SHA-256(DER), in groups of 4. */
    fun fingerprint(der: ByteArray): String {
        val hex = MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02X".format(it) }.take(20)
        return hex.chunked(4).joinToString("-")
    }
}
