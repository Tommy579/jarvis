/// Module performant Jarvis Core en Rust
/// Fournit des utilitaires optimisés pour le traitement intensif et l'asynchronisme.

use std::sync::atomic::{AtomicU64, Ordering};

static OPERATION_COUNT: AtomicU64 = AtomicU64::new(0);

/// Incrémente et retourne le nombre d'opérations exécutées par le module Rust.
pub fn increment_operations() -> u64 {
    OPERATION_COUNT.fetch_add(1, Ordering::SeqCst)
}

/// Retourne le nombre total d'opérations enregistrées.
pub fn get_operation_count() -> u64 {
    OPERATION_COUNT.load(Ordering::SeqCst)
}

/// Fonction de calcul haute performance (somme parallèle ou vectorisée simulée).
pub fn fast_compute(data: &[u64]) -> u64 {
    increment_operations();
    data.iter().fold(0, |acc, &x| acc.wrapping_add(x))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compute() {
        let data = vec![1, 2, 3, 4, 5];
        assert_eq!(fast_compute(&data), 15);
        assert!(get_operation_count() > 0);
    }
}
