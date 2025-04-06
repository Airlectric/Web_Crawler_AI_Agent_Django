import numpy as np
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler
import joblib
import os
import re

class OnlineLearningCrawler:
    def __init__(self, model_file='models/online_model.pkl', scaler_file='models/scaler.pkl', updates_file='models/total_updates.pkl'):
        self.model_file = model_file
        self.scaler_file = scaler_file
        self.updates_file = updates_file
        self.model = None
        self.scaler = StandardScaler()
        self.classes = [0, 1]  # 0: irrelevant, 1: relevant
        self.total_updates = 0
        self.load_model()

    def load_model(self):
        """Load the model, scaler, and total_updates if they exist, otherwise initialize new ones."""
        if os.path.exists(self.model_file) and os.path.exists(self.scaler_file):
            self.model = joblib.load(self.model_file)
            self.scaler = joblib.load(self.scaler_file)
            if os.path.exists(self.updates_file):
                self.total_updates = joblib.load(self.updates_file)
                print(f"Loaded model with {self.total_updates} prior updates")
            else:
                print("Loaded model, but no prior updates found")
        else:
            self.model = SGDClassifier(loss='log_loss', random_state=42)
            print("Initialized new model")

    def save_model(self):
        """Save the model, scaler, and total_updates to disk."""
        joblib.dump(self.model, self.model_file)
        joblib.dump(self.scaler, self.scaler_file)
        joblib.dump(self.total_updates, self.updates_file)

    def extract_features(self, url: str, anchor_text: str, parent_relevance: float) -> dict:
        """Extract an expanded set of features from a link with weighted high-priority keywords."""
        # General keywords (lower weight)
        general_keywords = [
            'energy', 'centre', 'center', 'startup', 'programme', 'institute', 'academic', 'calendar',
            'education', 'faculty', 'department', 'project', 'course', 'study', 'scholarship',
            'conference', 'seminar', 'workshop', 'development', 'training', 'graduate', 'undergraduate',
            'admissions', 'curriculum', 'technology', 'science', 'clean', 'funds', 'funding'
        ]
        
        # High-priority keywords (higher weight)
        high_priority_keywords = [
            'research', 'laboratory', 'lab', 'innovation', 'publications', 'research-centre'
        ]

        # Count occurrences
        general_keyword_count = sum(1 for kw in general_keywords if kw in url.lower())
        general_anchor_count = sum(1 for kw in general_keywords if kw in anchor_text.lower())
        
        # Count high-priority keywords and apply a weight of 5
        HIGH_PRIORITY_WEIGHT = 5
        high_priority_keyword_count = HIGH_PRIORITY_WEIGHT * sum(1 for kw in high_priority_keywords if kw in url.lower())
        high_priority_anchor_count = HIGH_PRIORITY_WEIGHT * sum(1 for kw in high_priority_keywords if kw in anchor_text.lower())

        # Additional features
        url_depth = url.count('/') - 2
        url_length = len(url)
        query_params = len(re.findall(r'\?', url))
        is_pdf = 1 if url.lower().endswith('.pdf') else 0

        print(f"Anchor text: {anchor_text}")
        return {
            'general_keyword_count': general_keyword_count,
            'general_anchor_count': general_anchor_count,
            'high_priority_keyword_count': high_priority_keyword_count,
            'high_priority_anchor_count': high_priority_anchor_count,
            'parent_relevance': parent_relevance,
            'url_depth': url_depth,
            'url_length': url_length,
            'query_params': query_params,
            'is_pdf': is_pdf
        }

    def predict(self, url: str, anchor_text: str, parent_relevance: float) -> float:
        """Predict the probability that a link is relevant."""
        if self.model is None or not hasattr(self.model, 'coef_'):
            return 0.5  # Default to 50% if no model yet

        features = self.extract_features(url, anchor_text, parent_relevance)
        feature_array = np.array([[
            features['general_keyword_count'],
            features['general_anchor_count'],
            features['high_priority_keyword_count'],
            features['high_priority_anchor_count'],
            features['parent_relevance'],
            features['url_depth'],
            features['url_length'],
            features['query_params'],
            features['is_pdf']
        ]])
        scaled_features = self.scaler.transform(feature_array)
        prob = self.model.predict_proba(scaled_features)[0][1]
        return prob

    def update_model(self, url: str, anchor_text: str, parent_relevance: float, label: int):
        """Update the model with a new data point."""
        features = self.extract_features(url, anchor_text, parent_relevance)
        feature_array = np.array([[
            features['general_keyword_count'],
            features['general_anchor_count'],
            features['high_priority_keyword_count'],
            features['high_priority_anchor_count'],
            features['parent_relevance'],
            features['url_depth'],
            features['url_length'],
            features['query_params'],
            features['is_pdf']
        ]])

        if not hasattr(self.scaler, 'mean_'):
            self.scaler.partial_fit(feature_array)
        scaled_features = self.scaler.transform(feature_array)

        self.model.partial_fit(scaled_features, [label], classes=self.classes)
        self.total_updates += 1
        print(f"Model updated. Total Updates: {self.total_updates}, Label: {label}, Features: {features}")
        self.save_model()