import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

class PerformanceDashboard:
    def __init__(self, results_file="results.csv"):
        self.df = pd.read_csv(results_file)

    def plot_success_rate(self):
        fig, axes = plt.subplots(2, 2, figsize=(12, 10))

        # Success rate over time
        self.df['timestamp'] = pd.to_datetime(self.df['timestamp'])
        self.df.set_index('timestamp')['Submission status'].resample('H').apply(
            lambda x: (x == 'success').mean()
        ).plot(ax=axes[0,0], title='Success Rate Over Time')

        # Fields filled distribution
        self.df['fields_filled'].hist(ax=axes[0,1], bins=20, title='Fields Filled Distribution')

        # Processing time
        self.df['processing_time'].plot(kind='box', ax=axes[1,0], title='Processing Time Distribution')

        # Error reasons
        error_counts = self.df[self.df['Submission status'] != 'success']['reason'].value_counts().head(10)
        error_counts.plot(kind='bar', ax=axes[1,1], title='Top 10 Error Reasons')

        plt.tight_layout()
        plt.savefig('performance_report.png')
        plt.show()
